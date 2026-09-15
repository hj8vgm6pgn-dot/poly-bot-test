import time
from db import conn

VERSION="0.8-pair"

def open_pair():
    with conn() as c:
        r=c.execute("""SELECT * FROM pair_positions WHERE status='OPEN'
                       AND strategy_version=? ORDER BY id DESC LIMIT 1""",(VERSION,)).fetchone()
    return dict(r) if r else None

def has_pair(market_id):
    with conn() as c:
        return c.execute("""SELECT 1 FROM pair_positions
                            WHERE market_id=? AND strategy_version=? LIMIT 1""",
                         (market_id,VERSION)).fetchone() is not None

def enter_pair(now,market,up_price,down_price,pair_budget):
    total=float(up_price)+float(down_price)
    if total>=1.0 or total<=0:return None
    shares=float(pair_budget)/total
    with conn() as c:
        c.execute("""INSERT OR IGNORE INTO pair_positions(
            ts,market_id,market_slug,shares,up_entry,down_entry,entry_total,status,strategy_version
        ) VALUES(?,?,?,?,?,?,?,'OPEN',?)""",
        (now,market["market_id"],market["slug"],shares,float(up_price),float(down_price),total,VERSION))
        r=c.execute("""SELECT * FROM pair_positions WHERE market_id=? AND strategy_version=?""",
                    (market["market_id"],VERSION)).fetchone()
    return dict(r) if r else None

def exit_pair(pair,up_bid,down_bid,reason="combined bids >= 1"):
    total=float(up_bid)+float(down_bid)
    proceeds=float(pair["shares"])*total
    cost=float(pair["shares"])*float(pair["entry_total"])
    pnl=proceeds-cost
    with conn() as c:
        c.execute("""UPDATE pair_positions SET status='CLOSED',exit_up=?,exit_down=?,
                     exit_total=?,pnl=?,exit_reason=?,closed_ts=? WHERE id=? AND status='OPEN'""",
                  (float(up_bid),float(down_bid),total,pnl,reason,int(time.time()),pair["id"]))
    return pnl

def settle_pair(pair):
    # One UP+DOWN pair settles to exactly $1 when the market resolves.
    proceeds=float(pair["shares"])
    cost=float(pair["shares"])*float(pair["entry_total"])
    pnl=proceeds-cost
    with conn() as c:
        c.execute("""UPDATE pair_positions SET status='CLOSED',exit_total=1.0,pnl=?,
                     exit_reason='settlement',closed_ts=? WHERE id=? AND status='OPEN'""",
                  (pnl,int(time.time()),pair["id"]))
    return pnl

def total_pnl():
    with conn() as c:
        r=c.execute("""SELECT COALESCE(SUM(pnl),0) p FROM pair_positions
                       WHERE status='CLOSED' AND strategy_version=?""",(VERSION,)).fetchone()
    return float(r["p"])

def stats():
    with conn() as c:
        rows=c.execute("SELECT * FROM pair_positions WHERE strategy_version=?",(VERSION,)).fetchall()
    closed=[r for r in rows if r["status"]=="CLOSED"]
    pnl=sum(float(r["pnl"] or 0) for r in closed)
    early=sum(r["exit_reason"]=="combined bids >= 1" for r in closed)
    settled=sum(r["exit_reason"]=="settlement" for r in closed)
    avg_discount=(sum(1-float(r["entry_total"]) for r in rows)/len(rows)) if rows else 0
    return {"entries":len(rows),"open":sum(r["status"]=="OPEN" for r in rows),
            "closed":len(closed),"early_exits":early,"settlements":settled,
            "pnl":pnl,"avg_entry_discount":avg_discount}
