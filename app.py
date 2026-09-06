import os, time, asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from db import init_db, conn, log
from strategy import decide
from polymarket import discover_current_btc5m, get_book, book_metrics, resolved_winner
from price_feed import ProxyTwapFeed

load_dotenv()
init_db()

MODE=os.getenv("BOT_MODE","paper").lower()
START=float(os.getenv("STARTING_BANKROLL",1000))
RISK=float(os.getenv("RISK_PER_TRADE_PCT",1))/100
MAX_DAILY=float(os.getenv("MAX_DAILY_LOSS_PCT",5))/100
MIN_EDGE=float(os.getenv("MIN_EDGE",.08))
MAX_ENTRY=float(os.getenv("MAX_ENTRY_PRICE",.72))
MIN_SECS=int(os.getenv("MIN_SECONDS_LEFT",30))
MAX_SECS=int(os.getenv("MAX_SECONDS_LEFT",120))
MAX_SPREAD=float(os.getenv("MAX_SPREAD",.05))
MIN_LIQ=float(os.getenv("MIN_BOOK_LIQUIDITY_USD",50))
MIN_MOVE=float(os.getenv("MIN_ABS_TWAP_MOVE_BPS",2))
POLL=float(os.getenv("BOT_POLL_SECONDS",2.0))
FEED_MODE=os.getenv("PRICE_FEED_MODE","proxy").lower()

feed=ProxyTwapFeed()
market=None
market_start_twap={}
stopped=False
last_status={"message":"Starting bot…"}
bg_task=None

def pnl_today():
    cutoff=int(time.time())-86400
    with conn() as c:
        r=c.execute("SELECT COALESCE(SUM(pnl),0) p FROM trades WHERE ts>=?",(cutoff,)).fetchone()
    return float(r["p"])

def already_traded(mid):
    with conn() as c:
        return c.execute("SELECT 1 FROM trades WHERE market_id=? LIMIT 1",(mid,)).fetchone() is not None

def settle_trade(slug, winner):
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE market_slug=? AND status='OPEN'",(slug,)).fetchall()
        for r in rows:
            pnl=(r["shares"]-r["stake"]) if r["side"]==winner else -r["stake"]
            c.execute("UPDATE trades SET status='CLOSED',winner=?,pnl=? WHERE id=?",
                      (winner,pnl,r["id"]))
            log("INFO",f"Resolved {slug}: {winner}. Trade {r['side']} P&L ${pnl:.2f}")

async def bot_loop():
    global market,last_status
    log("INFO","v0.2 background bot started")
    while True:
        try:
            await feed.update()
            now=int(time.time())

            # Discover / rotate market.
            new_market=await discover_current_btc5m(now)
            if new_market and (not market or market["slug"]!=new_market["slug"]):
                old=market
                market=new_market
                log("INFO",f"Market detected: {market['slug']}")
                # Freeze a proxy reference as close as possible to market start.
                # If the service joined late, this is flagged in dashboard.
                if market["slug"] not in market_start_twap:
                    market_start_twap[market["slug"]]=feed.twap(60)

                if old:
                    try:
                        winner=await resolved_winner(old["slug"])
                        if winner:
                            settle_trade(old["slug"],winner)
                    except Exception as e:
                        log("WARN",f"Resolution check failed: {e}")

            # Resolve any lingering open paper trades.
            with conn() as c:
                open_slugs=[r["market_slug"] for r in c.execute(
                    "SELECT DISTINCT market_slug FROM trades WHERE status='OPEN'").fetchall()]
            for slug in open_slugs[:5]:
                if slug and (not market or slug!=market["slug"] or now>market["end_ts"]+10):
                    try:
                        winner=await resolved_winner(slug)
                        if winner:settle_trade(slug,winner)
                    except Exception:
                        pass

            if not market:
                last_status={"mode":MODE,"message":"Searching for BTC 5m market…","stopped":stopped}
                await asyncio.sleep(POLL); continue

            seconds_left=market["end_ts"]-now
            current_twap=feed.twap(60)
            start_twap=market_start_twap.get(market["slug"])

            if current_twap is None or start_twap is None:
                last_status={"mode":MODE,"message":"Building 60s proxy TWAP…","stopped":stopped,
                             "market":market}
                await asyncio.sleep(POLL); continue

            # Only query book if market is current and still open.
            if not (market["start_ts"] <= now < market["end_ts"]):
                last_status={"mode":MODE,"message":"Waiting for next 5m window…","stopped":stopped,
                             "market":market,"seconds_left":seconds_left}
                await asyncio.sleep(POLL); continue

            ub,db=await asyncio.gather(get_book(market["up_token_id"]),
                                      get_book(market["down_token_id"]))
            ua,ubid,us,ul=book_metrics(ub)
            da,dbid,ds,dl=book_metrics(db)

            sig=decide(start_twap,current_twap,seconds_left,ua,da,us,ds,ul,dl,
                       feed.recent_vol_bps(),MIN_EDGE,MAX_ENTRY,MIN_SECS,MAX_SECS,
                       MAX_SPREAD,MIN_LIQ,MIN_MOVE)

            pnl=pnl_today()
            bankroll=START+pnl
            stake=max(1,bankroll*RISK)
            blocked=None

            if stopped:blocked="Emergency stop active"
            elif MODE!="paper":blocked="Live execution disabled in v0.2"
            elif pnl<=-(START*MAX_DAILY):blocked="Daily loss limit reached"
            elif already_traded(market["market_id"]):blocked="Already traded this market"

            executed=False
            if sig.action=="BUY" and not blocked:
                shares=stake/sig.market_price
                with conn() as c:
                    c.execute("""INSERT OR IGNORE INTO trades(
                        ts,market_id,market_slug,side,price,stake,shares,probability,edge,mode
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (now,market["market_id"],market["slug"],sig.side,sig.market_price,
                     stake,shares,sig.probability,sig.edge,MODE))
                    executed=c.total_changes>0
                if executed:
                    log("TRADE",f"PAPER BUY {sig.side} ${stake:.2f} @ {sig.market_price:.3f} | edge {sig.edge:.1%}")

            age_at_capture=max(0, int(time.time())-market["start_ts"])
            last_status={
                "mode":MODE,"feed_mode":FEED_MODE,"feed_source":feed.source,
                "settlement_feed_warning":"Proxy feed: NOT Chainlink settlement TWAP",
                "stopped":stopped,"market":{k:v for k,v in market.items() if k not in ("event","market")},
                "seconds_left":seconds_left,"start_twap":start_twap,"current_twap":current_twap,
                "start_reference_age_seconds":age_at_capture,
                "move_bps":(current_twap/start_twap-1)*10000,
                "up":{"ask":ua,"bid":ubid,"spread":us,"liquidity":ul},
                "down":{"ask":da,"bid":dbid,"spread":ds,"liquidity":dl},
                "signal":sig.__dict__,"blocked":blocked,"paper_trade_executed":executed,
                "daily_pnl":pnl,"bankroll":bankroll,"next_stake":stake
            }

        except Exception as e:
            last_status={"mode":MODE,"message":f"Bot loop error: {type(e).__name__}: {e}",
                         "stopped":stopped}
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

app=FastAPI(title="BTC 5m Polymarket Bot",lifespan=lifespan)

@app.post("/api/stop")
async def stop():
    global stopped
    stopped=True
    log("WARN","Emergency stop enabled")
    return {"ok":True,"stopped":True}

@app.post("/api/resume")
async def resume():
    global stopped
    stopped=False
    log("INFO","Bot resumed")
    return {"ok":True,"stopped":False}

@app.get("/api/status")
async def status():
    return last_status

@app.get("/api/trades")
async def trades():
    with conn() as c:
        rows=c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/events")
async def events():
    with conn() as c:
        rows=c.execute("SELECT * FROM events ORDER BY rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/stats")
async def stats():
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE status='CLOSED'").fetchall()
    n=len(rows)
    wins=sum(1 for r in rows if float(r["pnl"])>0)
    pnl=sum(float(r["pnl"]) for r in rows)
    return {"closed_trades":n,"wins":wins,"losses":n-wins,
            "win_rate":wins/n if n else 0,"total_pnl":pnl}

@app.get("/",response_class=HTMLResponse)
async def dashboard():
    return HTML

HTML=r"""<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>BTC 5M Bot</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#090c0f;color:#f7f7f8;margin:0;padding:20px}
.wrap{max-width:680px;margin:auto}.card{background:#171b20;border:1px solid #222831;border-radius:20px;padding:18px;margin:12px 0}
h1{font-size:28px;margin:12px 0 18px}.big{font-size:35px;font-weight:800}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.muted{color:#9ca3af}.warn{color:#fbbf24;font-size:13px}.good{color:#4ade80}.bad{color:#fb7185}.pill{display:inline-block;border-radius:999px;background:#272d35;padding:5px 9px;font-size:12px}
button{border:0;border-radius:16px;padding:15px;font-weight:800;font-size:16px;width:100%}.stop{background:#ef4444;color:white}.go{background:#22c55e;color:#07140b}
.row{display:flex;justify-content:space-between;gap:12px;margin:5px 0}.trade{font-size:13px;padding:8px 0;border-bottom:1px solid #293039}
</style></head><body><div class="wrap">
<h1>BTC 5M Bot <span id="mode" class="pill">paper</span></h1>
<div class="card"><div class="muted">Current market</div><div id="market">Searching…</div><div id="feed" class="warn"></div></div>
<div class="card"><div class="muted">Signal</div><div id="sig" class="big">Starting…</div><div id="why"></div><div id="blocked" class="warn"></div></div>
<div class="grid">
<div class="card"><div class="muted">Time left</div><div id="time" class="big">—</div></div>
<div class="card"><div class="muted">TWAP move</div><div id="move" class="big">—</div></div>
</div>
<div class="grid">
<div class="card"><div class="muted">UP ask</div><div id="up" class="big">—</div></div>
<div class="card"><div class="muted">DOWN ask</div><div id="down" class="big">—</div></div>
</div>
<div class="card">
<div class="row"><span>Probability</span><b id="prob">—</b></div>
<div class="row"><span>Estimated edge</span><b id="edge">—</b></div>
<div class="row"><span>Next stake</span><b id="stake">—</b></div>
<div class="row"><span>Bankroll</span><b id="bank">—</b></div>
<div class="row"><span>Daily P&L</span><b id="pnl">—</b></div>
</div>
<div class="grid"><button class="stop" onclick="fetch('/api/stop',{method:'POST'})">STOP</button><button class="go" onclick="fetch('/api/resume',{method:'POST'})">RESUME</button></div>
<div class="card"><b>Performance</b><div id="stats" class="muted">No resolved trades yet</div></div>
<div class="card"><b>Recent trades</b><div id="trades" class="muted">No trades yet</div></div>
</div>
<script>
function money(x){return '$'+Number(x||0).toFixed(2)}
async function tick(){
 try{
 let x=await fetch('/api/status').then(r=>r.json());
 mode.textContent=(x.mode||'paper').toUpperCase();
 market.textContent=x.market?.slug||x.message||'Searching…';
 feed.textContent=x.settlement_feed_warning||'';
 if(x.signal){
   sig.textContent=x.signal.action+(x.signal.side?' '+x.signal.side:'');
   sig.className='big '+(x.signal.action==='BUY'?'good':'');
   why.textContent=x.signal.reason||'';
   time.textContent=(x.seconds_left??0)+'s';
   move.textContent=(x.move_bps??0).toFixed(2)+' bp';
   up.textContent=(x.up?.ask??0).toFixed(3);
   down.textContent=(x.down?.ask??0).toFixed(3);
   prob.textContent=((x.signal.probability||0)*100).toFixed(1)+'%';
   edge.textContent=((x.signal.edge||0)*100).toFixed(1)+'%';
   stake.textContent=money(x.next_stake);bank.textContent=money(x.bankroll);pnl.textContent=money(x.daily_pnl);
   blocked.textContent=x.blocked?('BLOCKED: '+x.blocked):'';
 }else{sig.textContent=x.message||'Waiting…';why.textContent='';blocked.textContent=''}
 let st=await fetch('/api/stats').then(r=>r.json());
 stats.textContent=st.closed_trades?`${st.wins}-${st.losses} | ${(st.win_rate*100).toFixed(1)}% win rate | P&L ${money(st.total_pnl)}`:'No resolved trades yet';
 let tr=await fetch('/api/trades').then(r=>r.json());
 trades.innerHTML=tr.slice(0,8).map(t=>`<div class="trade">${t.side} ${money(t.stake)} @ ${Number(t.price).toFixed(3)} — ${t.status}${t.status==='CLOSED'?' — '+money(t.pnl):''}</div>`).join('')||'No trades yet';
 }catch(e){sig.textContent='Dashboard reconnecting…'}
}
setInterval(tick,2000);tick();
</script></body></html>"""
