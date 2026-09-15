import time
from db import conn

VERSION="0.8-pair-maker"
POSITION_VERSION="0.8-pair"

def maker_price(bid,ask,tick=.01):
    if bid is None or ask is None:return None
    bid=float(bid); ask=float(ask)
    if bid<=0 or ask<=0 or bid>=ask:return None
    px=bid+tick if bid+tick<ask else bid
    return round(px,2)

def active_order():
    with conn() as c:
        r=c.execute("""SELECT * FROM pair_orders WHERE status IN ('RESTING','ONE_LEG')
                       AND strategy_version=? ORDER BY id DESC LIMIT 1""",(VERSION,)).fetchone()
    return dict(r) if r else None

def has_attempt(market_id):
    with conn() as c:
        return c.execute("""SELECT 1 FROM pair_orders WHERE market_id=? AND strategy_version=?""",
                         (market_id,VERSION)).fetchone() is not None

def post_pair(now,market,up_bid,up_ask,down_bid,down_ask,pair_budget):
    up=maker_price(up_bid,up_ask); down=maker_price(down_bid,down_ask)
    if up is None or down is None:return None
    total=up+down
    # Never post a pair whose locked acquisition price is >= $1.
    if total>=1.0:return None
    shares=float(pair_budget)/total
    with conn() as c:
        c.execute("""INSERT OR IGNORE INTO pair_orders(
          ts,market_id,market_slug,target_shares,up_limit,down_limit,combined_limit,
          updated_ts,strategy_version) VALUES(?,?,?,?,?,?,?,?,?)""",
          (now,market["market_id"],market["slug"],shares,up,down,total,now,VERSION))
        r=c.execute("""SELECT * FROM pair_orders WHERE market_id=? AND strategy_version=?""",
                    (market["market_id"],VERSION)).fetchone()
    return dict(r) if r else None

def _leg_fills(limit_price,current_ask):
    # Conservative paper maker fill: later ask must cross our resting limit.
    return current_ask is not None and float(current_ask)<=float(limit_price)

def update_order(order,up_ask,down_ask):
    now=int(time.time())
    up_status=order["up_status"]; down_status=order["down_status"]
    up_ts=order["up_filled_ts"]; down_ts=order["down_filled_ts"]
    if up_status=="RESTING" and _leg_fills(order["up_limit"],up_ask):
        up_status="FILLED"; up_ts=now
    if down_status=="RESTING" and _leg_fills(order["down_limit"],down_ask):
        down_status="FILLED"; down_ts=now
    status="FILLED" if up_status=="FILLED" and down_status=="FILLED" else ("ONE_LEG" if "FILLED" in (up_status,down_status) else "RESTING")
    with conn() as c:
        c.execute("""UPDATE pair_orders SET up_status=?,down_status=?,up_filled_ts=?,
                     down_filled_ts=?,status=?,updated_ts=? WHERE id=?""",
                  (up_status,down_status,up_ts,down_ts,status,now,order["id"]))
    return status

def cancel_unfilled(order,reason):
    # A completely unfilled pair can be safely cancelled. A one-leg fill cannot:
    # it is explicitly marked STRANDED so paper P&L never pretends an arbitrage completed.
    status="STRANDED" if "FILLED" in (order["up_status"],order["down_status"]) else "CANCELLED"
    with conn() as c:
        c.execute("""UPDATE pair_orders SET status=?,cancel_reason=?,updated_ts=?
                     WHERE id=? AND status IN ('RESTING','ONE_LEG')""",
                  (status,reason,int(time.time()),order["id"]))

def create_position(order):
    total=float(order["up_limit"])+float(order["down_limit"])
    with conn() as c:
        c.execute("""INSERT OR IGNORE INTO pair_positions(
          ts,market_id,market_slug,shares,up_entry,down_entry,entry_total,status,strategy_version
        ) VALUES(?,?,?,?,?,?,?,'OPEN',?)""",
        (int(time.time()),order["market_id"],order["market_slug"],order["target_shares"],
         order["up_limit"],order["down_limit"],total,POSITION_VERSION))
        r=c.execute("""SELECT * FROM pair_positions WHERE market_id=? AND strategy_version=?""",
                    (order["market_id"],POSITION_VERSION)).fetchone()
    return dict(r) if r else None

def stats():
    with conn() as c:
        rows=c.execute("SELECT * FROM pair_orders WHERE strategy_version=?",(VERSION,)).fetchall()
    n=len(rows)
    return {"attempts":n,"paired":sum(r["status"]=="FILLED" for r in rows),
            "resting":sum(r["status"]=="RESTING" for r in rows),
            "one_leg":sum(r["status"]=="ONE_LEG" for r in rows),
            "stranded":sum(r["status"]=="STRANDED" for r in rows),
            "cancelled":sum(r["status"]=="CANCELLED" for r in rows),
            "pair_fill_rate":sum(r["status"]=="FILLED" for r in rows)/n if n else 0}
