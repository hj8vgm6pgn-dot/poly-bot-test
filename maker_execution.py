import time
from db import conn

VERSION="0.7-maker"

def resting_order():
    with conn() as c:
        r=c.execute("""SELECT * FROM maker_orders WHERE status='RESTING' AND strategy_version=?
                       ORDER BY id DESC LIMIT 1""",(VERSION,)).fetchone()
    return dict(r) if r else None

def has_order(market_id):
    with conn() as c:
        return c.execute("""SELECT 1 FROM maker_orders WHERE market_id=? AND strategy_version=? LIMIT 1""",
                         (market_id,VERSION)).fetchone() is not None

def maker_price(best_bid,best_ask,tick=0.01):
    if best_bid is None or best_ask is None:return None
    bid=float(best_bid); ask=float(best_ask)
    if bid<=0 or ask<=0 or bid>=ask:return None
    px=bid+tick if bid+tick<ask else bid
    return round(max(tick,min(1-tick,px)),2)

def post_order(now,market,side,limit_price,stake,probability,edge,bid,ask,seconds_left):
    with conn() as c:
        c.execute("""INSERT OR IGNORE INTO maker_orders(
        ts,market_id,market_slug,side,limit_price,target_stake,status,signal_probability,
        signal_edge,posted_bid,posted_ask,last_bid,last_ask,entry_seconds_left,updated_ts,strategy_version
        ) VALUES(?,?,?,?,?,?,'RESTING',?,?,?,?,?,?,?,?,?)""",
        (now,market["market_id"],market["slug"],side,limit_price,stake,probability,edge,
         bid,ask,bid,ask,seconds_left,now,VERSION))
        r=c.execute("SELECT * FROM maker_orders WHERE market_id=? AND strategy_version=?",
                    (market["market_id"],VERSION)).fetchone()
    return dict(r) if r else None

def cancel_order(order_id,reason):
    with conn() as c:
        c.execute("""UPDATE maker_orders SET status='CANCELLED',cancel_reason=?,updated_ts=?
                     WHERE id=? AND status='RESTING'""",(reason,int(time.time()),order_id))

def expire_market(market_slug):
    with conn() as c:
        c.execute("""UPDATE maker_orders SET status='EXPIRED',cancel_reason='Entry window expired',
                     updated_ts=? WHERE market_slug=? AND status='RESTING' AND strategy_version=?""",
                  (int(time.time()),market_slug,VERSION))

def evaluate_fill(order,current_ask):
    # Conservative: a later ask must trade down to/through our resting BUY limit.
    # Merely sitting at the best bid never grants assumed queue priority.
    return bool(order and current_ask is not None and float(current_ask)<=float(order["limit_price"]))

def update_seen(order_id,bid,ask):
    with conn() as c:
        c.execute("UPDATE maker_orders SET last_bid=?,last_ask=?,updated_ts=? WHERE id=?",
                  (bid,ask,int(time.time()),order_id))

def fill_order(order,now,current_bid,current_ask,ctx):
    stake=float(order["target_stake"]); price=float(order["limit_price"]); shares=stake/price
    with conn() as c:
        c.execute("""UPDATE maker_orders SET status='FILLED',last_bid=?,last_ask=?,
                     fill_model='ask_crossed_resting_limit',filled_ts=?,updated_ts=?
                     WHERE id=? AND status='RESTING'""",
                  (current_bid,current_ask,now,now,order["id"]))
        c.execute("""INSERT OR IGNORE INTO trades(
        ts,market_id,market_slug,side,price,stake,shares,probability,edge,mode,
        start_twap,entry_twap,entry_move_bps,entry_seconds_left,entry_spread,entry_liquidity,
        entry_feed_quality,raw_model_probability,market_implied_probability,blended_probability,
        model_market_gap,strategy_version,lag_score,book_imbalance,contract_velocity,
        spot_mom_5,spot_mom_10,spot_mom_20,spot_acceleration,source_disagreement_bps,
        execution_type,maker_order_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now,order["market_id"],order["market_slug"],order["side"],price,stake,shares,
         order["signal_probability"],order["signal_edge"],"paper",ctx["start_twap"],ctx["entry_twap"],
         ctx["move_bps"],ctx["seconds_left"],ctx["spread"],ctx["liquidity"],ctx["feed_quality"],
         ctx["raw_prob"],ctx["market_prob"],order["signal_probability"],ctx["model_market_gap"],
         VERSION,ctx["lag_score"],ctx["book_imbalance"],ctx["contract_velocity"],ctx["m5"],ctx["m10"],
         ctx["m20"],ctx["acceleration"],ctx["source_disagreement_bps"],"MAKER",order["id"]))

def stats():
    with conn() as c:
        rows=c.execute("SELECT * FROM maker_orders WHERE strategy_version=?",(VERSION,)).fetchall()
    total=len(rows); filled=sum(r["status"]=="FILLED" for r in rows); resting=sum(r["status"]=="RESTING" for r in rows)
    missed=sum(r["status"] in ("CANCELLED","EXPIRED") for r in rows)
    return {"signals_posted":total,"filled":filled,"resting":resting,"missed":missed,
            "fill_rate":filled/total if total else 0}
