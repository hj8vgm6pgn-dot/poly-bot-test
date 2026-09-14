import os,time,asyncio,json
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

from db import init_db,conn,log
from price_feed import MultiProxyFeed
from polymarket import discover_current_btc5m,get_book,book_metrics,store_book_obs,contract_velocity,book_imbalance,resolved_winner
from strategy import decide
from maker_execution import resting_order,has_order,maker_price,post_order,cancel_order,expire_market,evaluate_fill,update_seen,fill_order,stats as maker_stats

load_dotenv(); init_db()

MODE=os.getenv("BOT_MODE","paper").lower()
START=float(os.getenv("STARTING_BANKROLL",1000))
RISK=float(os.getenv("RISK_PER_TRADE_PCT",1))/100
MAX_DAILY=float(os.getenv("MAX_DAILY_LOSS_PCT",5))/100
MIN_SECS=int(os.getenv("MIN_SECONDS_LEFT",30))
MAX_SECS=int(os.getenv("MAX_SECONDS_LEFT",120))
MIN_EDGE=float(os.getenv("MIN_EDGE",.06))
MAX_ENTRY=float(os.getenv("MAX_ENTRY_PRICE",.88))
MIN_ENTRY=float(os.getenv("MIN_ENTRY_PRICE",.08))
MAX_SPREAD=float(os.getenv("MAX_SPREAD",.05))
MIN_LIQ=float(os.getenv("MIN_BOOK_LIQUIDITY_USD",50))
MIN_MOVE=float(os.getenv("MIN_ABS_TWAP_MOVE_BPS",1.5))
MIN_MODEL_PROB=float(os.getenv("MIN_MODEL_PROBABILITY",.56))
MODEL_PROB_CAP=float(os.getenv("MODEL_PROB_CAP",.90))
MARKET_BLEND=float(os.getenv("MARKET_BLEND_WEIGHT",.30))
MAX_GAP=float(os.getenv("MAX_MODEL_MARKET_GAP",.28))
LAG_MIN=float(os.getenv("LAG_MIN_SCORE",.58))
LAG_STRONG=float(os.getenv("LAG_STRONG_SCORE",.72))
MAX_DISAG=float(os.getenv("MAX_SOURCE_DISAGREEMENT_BPS",4))
MIN_SOURCES=float(os.getenv("MIN_ACTIVE_SOURCES",2))
EXTREME_P=float(os.getenv("EXTREME_PRICE_THRESHOLD",.12))
EXTREME_MOVE=float(os.getenv("EXTREME_MIN_MOVE_BPS",6))
EXTREME_SECS=int(os.getenv("EXTREME_MIN_SECONDS_LEFT",45))
POLL=float(os.getenv("BOT_POLL_SECONDS",1))

feed=MultiProxyFeed()
market=None; stopped=False; bg_task=None
last_status={"message":"Starting v0.7 maker-only…"}
last_logged_second=None

def pnl_today():
    cutoff=int(time.time())-86400
    with conn() as c:
        r=c.execute("""SELECT COALESCE(SUM(pnl),0) p FROM trades
                       WHERE ts>=? AND strategy_version='0.7-maker'""",(cutoff,)).fetchone()
    return float(r["p"])

def total_pnl():
    with conn() as c:
        r=c.execute("""SELECT COALESCE(SUM(pnl),0) p FROM trades
                       WHERE status='CLOSED' AND strategy_version='0.7-maker'""").fetchone()
    return float(r["p"])

def already_traded(mid):
    with conn() as c:
        return c.execute("SELECT 1 FROM trades WHERE market_id=? LIMIT 1",(mid,)).fetchone() is not None

def open_trade():
    with conn() as c:
        r=c.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None

def get_or_create_ref(m):
    with conn() as c:
        row=c.execute("SELECT * FROM market_refs WHERE market_slug=?",(m["slug"],)).fetchone()
    if row:return dict(row)
    twap,q=feed.twap_at(m["start_ts"],60)
    if twap is None:return None
    age=float(q.get("first_gap",0))
    method="exact-boundary proxy TWAP" if age<=2 and q.get("coverage",0)>=.9 else "partial-boundary proxy TWAP"
    with conn() as c:
        c.execute("""INSERT OR REPLACE INTO market_refs
        (market_slug,start_ts,start_twap,capture_method,ref_age_seconds,created_ts)
        VALUES(?,?,?,?,?,?)""",(m["slug"],m["start_ts"],twap,method,age,int(time.time())))
    return {"market_slug":m["slug"],"start_ts":m["start_ts"],"start_twap":twap,
            "capture_method":method,"ref_age_seconds":age}

def settle_trade(slug,winner):
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE market_slug=? AND status='OPEN'",(slug,)).fetchall()
        for r in rows:
            pnl=(r["shares"]-r["stake"]) if r["side"]==winner else -r["stake"]
            c.execute("UPDATE trades SET status='CLOSED',winner=?,pnl=?,resolved_ts=? WHERE id=?",
                      (winner,pnl,int(time.time()),r["id"]))
        c.execute("UPDATE signal_log SET winner=? WHERE market_slug=?",(winner,slug))

async def resolve_open():
    with conn() as c:
        slugs=[r["market_slug"] for r in c.execute(
            "SELECT DISTINCT market_slug FROM trades WHERE status='OPEN'").fetchall()]
    for slug in slugs[:10]:
        try:
            winner=await resolved_winner(slug)
            if winner:settle_trade(slug,winner)
        except Exception: pass

def log_signal(now,m,sig,move_bps,ua,da,moms,imb,vel,disag):
    with conn() as c:
        c.execute("""INSERT INTO signal_log(
        ts,market_id,market_slug,seconds_left,move_bps,up_ask,down_ask,
        raw_up_probability,blended_up_probability,market_up_probability,
        chosen_side,chosen_probability,market_price,edge,lag_score,book_imbalance,
        contract_velocity,spot_mom_5,spot_mom_10,spot_mom_20,spot_acceleration,
        source_disagreement_bps,action,reason
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now,m["market_id"],m["slug"],m["end_ts"]-now,move_bps,ua,da,
         sig.raw_up_probability,sig.blended_up_probability,sig.market_up_probability,
         sig.side,sig.probability,sig.market_price,sig.edge,sig.lag_score,imb,vel,
         moms["m5"],moms["m10"],moms["m20"],moms["acceleration"],disag,sig.action,sig.reason))

async def bot_loop():
    global market,last_status,last_logged_second
    log("INFO","v0.7 maker-only started")
    while True:
        try:
            await feed.update()
            now=int(time.time())
            await resolve_open()

            m=await discover_current_btc5m(now)
            if m and (not market or m["slug"]!=market["slug"]):
                market=m; last_logged_second=None
                log("INFO",f"Market detected {m['slug']}")

            if not market:
                last_status={"version":"0.7-maker","mode":MODE,"message":"Searching for current BTC 5m market…"}
                await asyncio.sleep(POLL); continue

            ref=get_or_create_ref(market)
            if not ref:
                last_status={"version":"0.7-maker","mode":MODE,"message":"Building boundary reference…","market":market}
                await asyncio.sleep(POLL); continue

            cur_twap,q=feed.twap_at(time.time(),60)
            if cur_twap is None:
                await asyncio.sleep(POLL); continue

            ub,db=await asyncio.gather(get_book(market["up_token_id"]),get_book(market["down_token_id"]))
            up=book_metrics(ub); down=book_metrics(db)
            store_book_obs(market["slug"],up,down)

            ua,ubid,us,ua_depth,ub_depth=up
            da,dbid,ds,da_depth,db_depth=down
            imb=book_imbalance(up,down)
            vel=contract_velocity(market["slug"],10)
            moms=feed.momentum_pack()
            seconds_left=market["end_ts"]-now
            move_bps=(cur_twap/ref["start_twap"]-1)*10000

            sig=decide(
                start_twap=ref["start_twap"],current_twap=cur_twap,seconds_left=seconds_left,
                up_ask=ua,down_ask=da,spread_up=us,spread_down=ds,
                liq_up=ua_depth,liq_down=da_depth,
                m5=moms["m5"],m10=moms["m10"],m20=moms["m20"],accel=moms["acceleration"],
                book_imbalance=imb,contract_velocity=vel,
                source_count=q.get("source_count",0),
                source_disagreement_bps=q.get("disagreement_bps",0),
                min_edge=MIN_EDGE,max_entry=MAX_ENTRY,min_entry=MIN_ENTRY,
                min_secs=MIN_SECS,max_secs=MAX_SECS,max_spread=MAX_SPREAD,min_liq=MIN_LIQ,
                min_abs_move=MIN_MOVE,min_model_prob=MIN_MODEL_PROB,
                model_prob_cap=MODEL_PROB_CAP,market_blend_weight=MARKET_BLEND,
                max_model_market_gap=MAX_GAP,lag_min_score=LAG_MIN,lag_strong_score=LAG_STRONG,
                max_source_disagreement_bps=MAX_DISAG,min_active_sources=MIN_SOURCES,
                extreme_price_threshold=EXTREME_P,extreme_min_move_bps=EXTREME_MOVE,
                extreme_min_seconds_left=EXTREME_SECS)

            if 0<=seconds_left<=150 and (last_logged_second is None or abs(seconds_left-last_logged_second)>=5):
                log_signal(now,market,sig,move_bps,ua,da,moms,imb,vel,q.get("disagreement_bps",0))
                last_logged_second=seconds_left

            daily_pnl=pnl_today(); bankroll=START+total_pnl(); stake=max(1,bankroll*RISK)
            blocked=None
            if stopped: blocked="Emergency stop active"
            elif MODE!="paper": blocked="Live execution disabled in v0.7-maker"
            elif daily_pnl<=-(START*MAX_DAILY): blocked="Daily loss limit reached"
            elif already_traded(market["market_id"]) or has_order(market["market_id"]): blocked="Already ordered/traded this market"
            elif ref["capture_method"]!="exact-boundary proxy TWAP":
                blocked="Boundary reference is not exact enough"

            # v0.7 maker-only: post at a resting bid; never cross and never fall back to taker.
            ro=resting_order()
            if ro and ro["market_slug"]!=market["slug"]:
                expire_market(ro["market_slug"]); ro=None

            if ro and ro["market_slug"]==market["slug"]:
                rb=ubid if ro["side"]=="UP" else dbid
                ra=ua if ro["side"]=="UP" else da
                update_seen(ro["id"],rb,ra)
                if seconds_left<MIN_SECS:
                    cancel_order(ro["id"],"Entry window closed"); ro=None
                elif evaluate_fill(ro,ra):
                    side_spread=us if ro["side"]=="UP" else ds
                    side_liq=ua_depth if ro["side"]=="UP" else da_depth
                    market_prob=sig.market_up_probability if ro["side"]=="UP" else 1-sig.market_up_probability
                    raw_prob=sig.raw_up_probability if ro["side"]=="UP" else 1-sig.raw_up_probability
                    qual=f"{ref['capture_method']}; coverage={q.get('coverage',0):.0%}; sources={q.get('source_count',0):.1f}"
                    fill_order(ro,now,rb,ra,{"start_twap":ref["start_twap"],"entry_twap":cur_twap,
                        "move_bps":move_bps,"seconds_left":seconds_left,"spread":side_spread,
                        "liquidity":side_liq,"feed_quality":qual,"raw_prob":raw_prob,
                        "market_prob":market_prob,"model_market_gap":sig.model_market_gap,
                        "lag_score":sig.lag_score,"book_imbalance":imb,"contract_velocity":vel,
                        "m5":moms["m5"],"m10":moms["m10"],"m20":moms["m20"],
                        "acceleration":moms["acceleration"],"source_disagreement_bps":q.get("disagreement_bps",0)})
                    ro=None
                elif sig.action!="BUY" or sig.side!=ro["side"]:
                    cancel_order(ro["id"],"Signal invalidated before fill"); ro=None

            if sig.action=="BUY" and not blocked and not ro:
                side_bid=ubid if sig.side=="UP" else dbid
                side_ask=ua if sig.side=="UP" else da
                px=maker_price(side_bid,side_ask)
                if px is not None:
                    post_order(now,market,sig.side,px,stake,sig.probability,sig.edge,
                               side_bid,side_ask,seconds_left)
                    log("INFO",f"Maker order posted {sig.side} {px:.2f} market={market['slug']}")

            last_status={
                "version":"0.7-maker","mode":MODE,"market":market,"stopped":stopped,
                "feed_warning":"Multi-exchange proxy — NOT exact Chainlink settlement feed",
                "start_ref_method":ref["capture_method"],"current_quality":q,
                "seconds_left":seconds_left,"move_bps":move_bps,
                "up":{"ask":ua,"bid":ubid,"spread":us,"liq":ua_depth},
                "down":{"ask":da,"bid":dbid,"spread":ds,"liq":da_depth},
                "momentum":moms,"book_imbalance":imb,"contract_velocity":vel,
                "signal":sig.__dict__,"blocked":blocked,
                "daily_pnl":daily_pnl,"bankroll":bankroll,"next_stake":stake,
                "open_trade":open_trade(),"maker_order":resting_order(),"maker_stats":maker_stats()
            }
        except Exception as e:
            last_status={"version":"0.7-maker","mode":MODE,
                         "message":f"Bot loop error: {type(e).__name__}: {e}"}
            log("ERROR",last_status["message"])
        await asyncio.sleep(POLL)

@asynccontextmanager
async def lifespan(app):
    global bg_task
    bg_task=asyncio.create_task(bot_loop())
    yield
    bg_task.cancel()
    try: await bg_task
    except BaseException: pass

app=FastAPI(title="BTC 5m Bot v0.7 Maker",lifespan=lifespan)

@app.post("/api/stop")
async def stop():
    global stopped
    stopped=True
    return {"ok":True}

@app.post("/api/resume")
async def resume():
    global stopped
    stopped=False
    return {"ok":True}

@app.get("/api/status")
async def status(): return last_status

@app.get("/api/trades")
async def trades():
    with conn() as c:
        rows=c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/stats")
async def stats():
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE status='CLOSED' AND strategy_version='0.7-maker'").fetchall()
    n=len(rows); wins=sum(1 for r in rows if float(r["pnl"])>0)
    pnl=sum(float(r["pnl"]) for r in rows)
    avg_edge=sum(float(r["edge"]) for r in rows)/n if n else 0
    avg_lag=sum(float(r["lag_score"] or 0) for r in rows)/n if n else 0
    ms=maker_stats()
    return {"closed_trades":n,"wins":wins,"losses":n-wins,
            "win_rate":wins/n if n else 0,"total_pnl":pnl,
            "avg_entry_edge":avg_edge,"avg_lag_score":avg_lag,
            "maker":ms}


@app.get("/api/public")
async def public_snapshot():
    """Single public read-only endpoint for remote monitoring."""
    with conn() as c:
        trade_rows=c.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT 10"
        ).fetchall()
        stat_rows=c.execute(
            "SELECT * FROM trades WHERE status='CLOSED' AND strategy_version='0.7-maker'"
        ).fetchall()

    n=len(stat_rows)
    wins=sum(1 for r in stat_rows if float(r["pnl"])>0)
    pnl=sum(float(r["pnl"]) for r in stat_rows)
    avg_edge=sum(float(r["edge"]) for r in stat_rows)/n if n else 0
    avg_lag=sum(float(r["lag_score"] or 0) for r in stat_rows)/n if n else 0

    snapshot=dict(last_status)
    snapshot["performance"]={
        "closed_trades":n,
        "wins":wins,
        "losses":n-wins,
        "win_rate":wins/n if n else 0,
        "total_pnl":pnl,
        "avg_entry_edge":avg_edge,
        "avg_lag_score":avg_lag
    }
    snapshot["recent_trades"]=[dict(r) for r in trade_rows]
    snapshot["server_ts"]=int(time.time())
    snapshot["monitoring_version"]="0.7-maker"
    return snapshot

@app.get("/api/healthz")
async def healthz():
    market_slug=None
    if isinstance(last_status,dict):
        m=last_status.get("market")
        if isinstance(m,dict):
            market_slug=m.get("slug")
    return {
        "ok": True,
        "version": "0.7-maker",
        "mode": MODE,
        "server_ts": int(time.time()),
        "market": market_slug
    }


def _fresh_monitor_payload():
    snapshot = dict(last_status) if isinstance(last_status, dict) else {}
    sig = snapshot.get("signal") or {}
    moms = snapshot.get("momentum") or {}
    up = snapshot.get("up") or {}
    down = snapshot.get("down") or {}
    quality = snapshot.get("current_quality") or {}
    market = snapshot.get("market") or {}

    return {
        "version": "0.7-maker",
        "mode": MODE,
        "server_ts": int(time.time()),
        "market_slug": market.get("slug"),
        "market_id": market.get("market_id"),
        "seconds_left": snapshot.get("seconds_left"),
        "twap_move_bps": snapshot.get("move_bps"),
        "up_ask": up.get("ask"),
        "up_bid": up.get("bid"),
        "up_spread": up.get("spread"),
        "up_liquidity": up.get("liq"),
        "down_ask": down.get("ask"),
        "down_bid": down.get("bid"),
        "down_spread": down.get("spread"),
        "down_liquidity": down.get("liq"),
        "momentum_5s_bps": moms.get("m5"),
        "momentum_10s_bps": moms.get("m10"),
        "momentum_20s_bps": moms.get("m20"),
        "acceleration_bps": moms.get("acceleration"),
        "book_imbalance": snapshot.get("book_imbalance"),
        "contract_velocity": snapshot.get("contract_velocity"),
        "action": sig.get("action"),
        "candidate_side": sig.get("side"),
        "reason": sig.get("reason"),
        "filters_passed": sig.get("filters_passed"),
        "filters_total": sig.get("filters_total"),
        "lag_score": sig.get("lag_score"),
        "lag_direction": sig.get("lag_direction"),
        "raw_up_probability": sig.get("raw_up_probability"),
        "market_up_probability": sig.get("market_up_probability"),
        "blended_up_probability": sig.get("blended_up_probability"),
        "model_market_gap": sig.get("model_market_gap"),
        "edge": sig.get("edge"),
        "blocked": snapshot.get("blocked"),
        "start_ref_method": snapshot.get("start_ref_method"),
        "coverage": quality.get("coverage"),
        "source_count": quality.get("source_count"),
        "source_disagreement_bps": quality.get("disagreement_bps"),
        "daily_pnl": snapshot.get("daily_pnl"),
        "bankroll": snapshot.get("bankroll"),
        "next_stake": snapshot.get("next_stake"),
        "open_trade": snapshot.get("open_trade"),
        "shadow_setup": (
            sig.get("action") == "SKIP"
            and (sig.get("lag_score") or 0) >= float(os.getenv("SHADOW_LAG_MIN_SCORE","0.30"))
            and (sig.get("lag_score") or 0) < float(os.getenv("LAG_MIN_SCORE","0.58"))
        ),
        "failed_filter_reason": sig.get("reason"),
    }


@app.get("/monitor.txt", response_class=PlainTextResponse)
async def monitor_txt():
    body = json.dumps(_fresh_monitor_payload(), separators=(",", ":"), default=str)
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Monitor-Version": "0.7-maker",
        },
    )

@app.get("/",response_class=HTMLResponse)
async def dashboard():
    snapshot = dict(last_status) if isinstance(last_status, dict) else {}
    sig = snapshot.get("signal") or {}
    moms = snapshot.get("momentum") or {}
    up = snapshot.get("up") or {}
    down = snapshot.get("down") or {}
    quality = snapshot.get("current_quality") or {}
    market = snapshot.get("market") or {}

    monitor = {
        "version": "0.7-maker",
        "mode": MODE,
        "server_ts": int(time.time()),
        "market_slug": market.get("slug"),
        "market_id": market.get("market_id"),
        "seconds_left": snapshot.get("seconds_left"),
        "twap_move_bps": snapshot.get("move_bps"),
        "up_ask": up.get("ask"),
        "up_bid": up.get("bid"),
        "up_spread": up.get("spread"),
        "up_liquidity": up.get("liq"),
        "down_ask": down.get("ask"),
        "down_bid": down.get("bid"),
        "down_spread": down.get("spread"),
        "down_liquidity": down.get("liq"),
        "momentum_5s_bps": moms.get("m5"),
        "momentum_10s_bps": moms.get("m10"),
        "momentum_20s_bps": moms.get("m20"),
        "acceleration_bps": moms.get("acceleration"),
        "book_imbalance": snapshot.get("book_imbalance"),
        "contract_velocity": snapshot.get("contract_velocity"),
        "action": sig.get("action"),
        "candidate_side": sig.get("side"),
        "reason": sig.get("reason"),
        "filters_passed": sig.get("filters_passed"),
        "filters_total": sig.get("filters_total"),
        "lag_score": sig.get("lag_score"),
        "lag_direction": sig.get("lag_direction"),
        "raw_up_probability": sig.get("raw_up_probability"),
        "market_up_probability": sig.get("market_up_probability"),
        "blended_up_probability": sig.get("blended_up_probability"),
        "model_market_gap": sig.get("model_market_gap"),
        "edge": sig.get("edge"),
        "blocked": snapshot.get("blocked"),
        "start_ref_method": snapshot.get("start_ref_method"),
        "coverage": quality.get("coverage"),
        "source_count": quality.get("source_count"),
        "source_disagreement_bps": quality.get("disagreement_bps"),
        "daily_pnl": snapshot.get("daily_pnl"),
        "bankroll": snapshot.get("bankroll"),
        "next_stake": snapshot.get("next_stake"),
        "open_trade": snapshot.get("open_trade"),
    }
    monitor_json = json.dumps(monitor, separators=(",", ":"), default=str)
    body = HTML.replace("__REMOTE_MONITOR_JSON__", monitor_json)
    return HTMLResponse(
        body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

HTML=r"""<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>BTC 5M Bot v0.7 Maker</title>
<style>
body{font-family:-apple-system;background:#090c0f;color:#f7f7f8;margin:0;padding:20px}
.wrap{max-width:680px;margin:auto}.card{background:#171b20;border:1px solid #252b33;border-radius:20px;padding:18px;margin:12px 0}
h1{font-size:28px}.big{font-size:34px;font-weight:800}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.muted{color:#9ca3af}.warn{color:#fbbf24}.pill{display:inline-block;border-radius:999px;background:#272d35;padding:5px 9px;font-size:12px}
.row{display:flex;justify-content:space-between;gap:12px;margin:7px 0}.small{font-size:13px}
button{border:0;border-radius:16px;padding:15px;font-weight:800;font-size:16px;width:100%}.stop{background:#ef4444;color:white}.go{background:#22c55e;color:#07140b}
.trade{font-size:13px;padding:9px 0;border-bottom:1px solid #293039}
</style></head><body><div class="wrap">
<h1>BTC 5M Bot <span class="pill">PAPER · v0.7 MAKER</span></h1>
<div class="card"><div class="muted">Current market</div><div id="market">Starting…</div><div id="feed" class="warn small"></div><div id="quality" class="muted small"></div></div>
<div class="card"><div class="muted">Current signal</div><div id="sig" class="big">Starting…</div><div id="why"></div><div id="blocked" class="warn"></div><div id="passes" class="muted small"></div></div>
<div class="grid"><div class="card"><div class="muted">Time left</div><div id="time" class="big">—</div></div><div class="card"><div class="muted">TWAP move</div><div id="move" class="big">—</div></div></div>
<div class="grid"><div class="card"><div class="muted">UP ask</div><div id="up" class="big">—</div></div><div class="card"><div class="muted">DOWN ask</div><div id="down" class="big">—</div></div></div>
<div class="card">
<div class="row"><span>5s momentum</span><b id="m5">—</b></div>
<div class="row"><span>10s momentum</span><b id="m10">—</b></div>
<div class="row"><span>20s momentum</span><b id="m20">—</b></div>
<div class="row"><span>Acceleration</span><b id="acc">—</b></div>
<div class="row"><span>Book imbalance</span><b id="imb">—</b></div>
<div class="row"><span>Contract velocity</span><b id="vel">—</b></div>
<div class="row"><span>Lag score</span><b id="lag">—</b></div>
<div class="row"><span>Lag direction</span><b id="lagdir">—</b></div>
</div>
<div class="card">
<div class="row"><span>Raw model UP</span><b id="raw">—</b></div>
<div class="row"><span>Market implied UP</span><b id="mkt">—</b></div>
<div class="row"><span>Blended UP</span><b id="blend">—</b></div>
<div class="row"><span>Model/market gap</span><b id="gap">—</b></div>
<div class="row"><span>Current edge</span><b id="edge">—</b></div>
<div class="row"><span>Source disagreement</span><b id="disag">—</b></div>
<div class="row"><span>Next stake</span><b id="stake">—</b></div>
<div class="row"><span>Bankroll</span><b id="bank">—</b></div>
</div>
<div id="makerCard" class="card" style="display:none"><div class="muted">Resting maker order</div><div id="makerSide" class="big"></div><div id="makerDetails"></div></div>
<div id="openCard" class="card" style="display:none"><div class="muted">Open paper position</div><div id="openSide" class="big"></div><div id="openDetails"></div></div>
<div class="grid"><button class="stop" onclick="fetch('/api/stop',{method:'POST'})">STOP</button><button class="go" onclick="fetch('/api/resume',{method:'POST'})">RESUME</button></div>
<div class="card"><b>v0.7 Maker Performance</b><div id="stats" class="muted"></div></div>
<div class="card"><b>Recent trades</b><div id="trades" class="muted"></div></div>
<div class="card small" id="remote-monitor-card">
<b>Remote monitor snapshot</b>
<div style="margin:8px 0"><a href="/monitor.txt" rel="nofollow">Fresh monitor.txt</a></div>
<pre style="white-space:pre-wrap;word-break:break-word;color:#9ca3af">__REMOTE_MONITOR_JSON__</pre>
</div>
</div>
<script>
function money(x){return '$'+Number(x||0).toFixed(2)}
function pct(x){return (Number(x||0)*100).toFixed(1)+'%'}
function bp(x){return Number(x||0).toFixed(2)+' bp'}
async function tick(){try{
let x=await fetch('/api/status').then(r=>r.json());
market.textContent=x.market?.slug||x.message||'Searching…';
feed.textContent=x.feed_warning||'';
quality.textContent=x.start_ref_method?`Start ref: ${x.start_ref_method} · coverage ${Math.round((x.current_quality?.coverage||0)*100)}% · sources ${(x.current_quality?.source_count||0).toFixed(1)}`:'';
if(x.signal){
sig.textContent=x.signal.action+(x.signal.side?' '+x.signal.side:'');
why.textContent=x.signal.reason||'';
blocked.textContent=x.blocked?('BLOCKED: '+x.blocked):'';
passes.textContent=`Filters passed: ${x.signal.filters_passed}/${x.signal.filters_total}`;
time.textContent=(x.seconds_left??0)+'s'; move.textContent=bp(x.move_bps);
up.textContent=(x.up?.ask??0).toFixed(3); down.textContent=(x.down?.ask??0).toFixed(3);
m5.textContent=bp(x.momentum?.m5); m10.textContent=bp(x.momentum?.m10); m20.textContent=bp(x.momentum?.m20);
acc.textContent=bp(x.momentum?.acceleration); imb.textContent=Number(x.book_imbalance||0).toFixed(2);
vel.textContent=Number(x.contract_velocity||0).toFixed(3); lag.textContent=Number(x.signal.lag_score||0).toFixed(2);
lagdir.textContent=x.signal.lag_direction||'—'; raw.textContent=pct(x.signal.raw_up_probability);
mkt.textContent=pct(x.signal.market_up_probability); blend.textContent=pct(x.signal.blended_up_probability);
gap.textContent=pct(x.signal.model_market_gap); edge.textContent=pct(x.signal.edge);
disag.textContent=bp(x.current_quality?.disagreement_bps); stake.textContent=money(x.next_stake); bank.textContent=money(x.bankroll);
}else sig.textContent=x.message||'Waiting…';
if(x.maker_order){makerCard.style.display='block';let o=x.maker_order;
makerSide.textContent=`${o.side} · ${money(o.target_stake)} @ ${Number(o.limit_price).toFixed(3)}`;
makerDetails.innerHTML=`RESTING MAKER · signal edge ${pct(o.signal_edge)} · ${o.entry_seconds_left}s at post`;
}else makerCard.style.display='none';
if(x.open_trade){openCard.style.display='block';let t=x.open_trade;
openSide.textContent=`${t.side} · ${money(t.stake)} @ ${Number(t.price).toFixed(3)}`;
openDetails.innerHTML=`Blended <b>${pct(t.blended_probability||t.probability)}</b> · edge <b>${pct(t.edge)}</b> · lag <b>${Number(t.lag_score||0).toFixed(2)}</b><br>${t.entry_seconds_left}s left · move ${Number(t.entry_move_bps||0).toFixed(2)} bp`;
}else openCard.style.display='none';
let st=await fetch('/api/stats').then(r=>r.json());
stats.textContent=(st.closed_trades?`${st.wins}-${st.losses} · ${(st.win_rate*100).toFixed(1)}% win rate · P&L ${money(st.total_pnl)} · avg edge ${(st.avg_entry_edge*100).toFixed(1)}% · avg lag ${Number(st.avg_lag_score||0).toFixed(2)} · `:'No resolved maker trades yet · ')+`maker fills ${st.maker?.filled||0}/${st.maker?.signals_posted||0} (${((st.maker?.fill_rate||0)*100).toFixed(1)}%)`;
let tr=await fetch('/api/trades').then(r=>r.json());
trades.innerHTML=tr.slice(0,8).map(t=>`<div class="trade"><b>${t.side}</b> ${money(t.stake)} @ ${Number(t.price).toFixed(3)} · ${(Number(t.probability)*100).toFixed(1)}% blended · ${(Number(t.edge)*100).toFixed(1)}% edge · lag ${Number(t.lag_score||0).toFixed(2)} · ${t.status}${t.status==='CLOSED'?' · '+money(t.pnl):''}</div>`).join('')||'No trades yet';
}catch(e){sig.textContent='Dashboard reconnecting…'}}
setInterval(tick,1500);tick();
</script></body></html>"""
