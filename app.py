import os,time,asyncio,json
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from db import init_db,conn,log
from strategy import decide
from polymarket import discover_current_btc5m,get_book,book_metrics,resolved_winner
from price_feed import MultiProxyFeed

load_dotenv(); init_db()

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
POLL=float(os.getenv("BOT_POLL_SECONDS",1))

feed=MultiProxyFeed()
market=None
stopped=False
last_status={"message":"Starting v0.3…"}
bg_task=None

def pnl_today():
    cutoff=int(time.time())-86400
    with conn() as c:
        r=c.execute("SELECT COALESCE(SUM(pnl),0) p FROM trades WHERE ts>=?",(cutoff,)).fetchone()
    return float(r["p"])

def already_traded(mid):
    with conn() as c:
        return c.execute("SELECT 1 FROM trades WHERE market_id=? LIMIT 1",(mid,)).fetchone() is not None

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
                     VALUES(?,?,?,?,?,?)""",
                  (m["slug"],m["start_ts"],twap,method,age,int(time.time())))
    return {"market_slug":m["slug"],"start_ts":m["start_ts"],"start_twap":twap,
            "capture_method":method,"ref_age_seconds":age}

def open_trade():
    with conn() as c:
        r=c.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None

def settle_trade(slug,winner):
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE market_slug=? AND status='OPEN'",(slug,)).fetchall()
        for r in rows:
            pnl=(r["shares"]-r["stake"]) if r["side"]==winner else -r["stake"]
            c.execute("""UPDATE trades SET status='CLOSED',winner=?,pnl=?,resolved_ts=?
                         WHERE id=?""",(winner,pnl,int(time.time()),r["id"]))
            log("INFO",f"Resolved {slug}: {winner}; {r['side']} P&L ${pnl:.2f}")

async def resolve_open():
    with conn() as c:
        slugs=[r["market_slug"] for r in c.execute(
            "SELECT DISTINCT market_slug FROM trades WHERE status='OPEN'").fetchall()]
    for slug in slugs[:10]:
        if not slug:continue
        try:
            winner=await resolved_winner(slug)
            if winner:settle_trade(slug,winner)
        except Exception:pass

async def bot_loop():
    global market,last_status
    log("INFO","v0.3 started")
    while True:
        try:
            await feed.update()
            now=int(time.time())
            await resolve_open()

            m=await discover_current_btc5m(now)
            if m and (not market or m["slug"]!=market["slug"]):
                market=m
                log("INFO",f"Market detected {m['slug']}")

            if not market:
                last_status={"mode":MODE,"message":"Searching for current BTC 5m market…","stopped":stopped}
                await asyncio.sleep(POLL);continue

            ref=get_or_create_ref(market)
            if not ref:
                last_status={"mode":MODE,"message":"Building exact boundary reference…",
                             "market":market,"stopped":stopped}
                await asyncio.sleep(POLL);continue

            seconds_left=market["end_ts"]-now
            current_twap,q=feed.twap_at(time.time(),60)
            if current_twap is None:
                last_status={"mode":MODE,"message":"Building 60s composite TWAP…","stopped":stopped}
                await asyncio.sleep(POLL);continue

            ub,db=await asyncio.gather(get_book(market["up_token_id"]),
                                      get_book(market["down_token_id"]))
            ua,ubid,us,ul=book_metrics(ub)
            da,dbid,ds,dl=book_metrics(db)

            sig=decide(ref["start_twap"],current_twap,seconds_left,ua,da,us,ds,ul,dl,
                       feed.recent_vol_bps(),MIN_EDGE,MAX_ENTRY,MIN_SECS,MAX_SECS,
                       MAX_SPREAD,MIN_LIQ,MIN_MOVE)

            pnl=pnl_today(); bankroll=START+pnl; stake=max(1,bankroll*RISK)
            blocked=None
            if stopped:blocked="Emergency stop active"
            elif MODE!="paper":blocked="Live execution disabled in v0.3"
            elif pnl<=-(START*MAX_DAILY):blocked="Daily loss limit reached"
            elif already_traded(market["market_id"]):blocked="Already traded this market"

            executed=False
            if sig.action=="BUY" and not blocked:
                shares=stake/sig.market_price
                move_bps=(current_twap/ref["start_twap"]-1)*10000
                side_spread=us if sig.side=="UP" else ds
                side_liq=ul if sig.side=="UP" else dl
                qual=f"{ref['capture_method']}; current coverage={q.get('coverage',0):.0%}; avg sources={q.get('source_count',0):.1f}"
                with conn() as c:
                    c.execute("""INSERT OR IGNORE INTO trades(
                        ts,market_id,market_slug,side,price,stake,shares,probability,edge,mode,
                        start_twap,entry_twap,entry_move_bps,entry_seconds_left,entry_spread,
                        entry_liquidity,entry_feed_quality
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (now,market["market_id"],market["slug"],sig.side,sig.market_price,stake,shares,
                     sig.probability,sig.edge,MODE,ref["start_twap"],current_twap,move_bps,
                     seconds_left,side_spread,side_liq,qual))
                    executed=c.total_changes>0
                if executed:
                    log("TRADE",f"PAPER {sig.side} ${stake:.2f} @ {sig.market_price:.3f}; edge {sig.edge:.1%}; {seconds_left}s left")

            ot=open_trade()
            last_status={
                "version":"0.3","mode":MODE,"stopped":stopped,
                "market":market,"seconds_left":seconds_left,
                "feed_source":feed.source,
                "feed_warning":"Multi-exchange proxy — NOT exact Chainlink settlement feed",
                "start_twap":ref["start_twap"],"start_ref_method":ref["capture_method"],
                "start_ref_age_seconds":ref["ref_age_seconds"],
                "current_twap":current_twap,"current_quality":q,
                "move_bps":(current_twap/ref["start_twap"]-1)*10000,
                "up":{"ask":ua,"bid":ubid,"spread":us,"liquidity":ul},
                "down":{"ask":da,"bid":dbid,"spread":ds,"liquidity":dl},
                "signal":sig.__dict__,"blocked":blocked,"paper_trade_executed":executed,
                "daily_pnl":pnl,"bankroll":bankroll,"next_stake":stake,
                "open_trade":ot
            }
        except Exception as e:
            last_status={"version":"0.3","mode":MODE,"stopped":stopped,
                         "message":f"Bot loop error: {type(e).__name__}: {e}"}
            log("ERROR",last_status["message"])
        await asyncio.sleep(POLL)

@asynccontextmanager
async def lifespan(app):
    global bg_task
    bg_task=asyncio.create_task(bot_loop())
    yield
    bg_task.cancel()
    try:await bg_task
    except BaseException:pass

app=FastAPI(title="BTC 5m Polymarket Bot v0.3",lifespan=lifespan)

@app.post("/api/stop")
async def stop():
    global stopped
    stopped=True;log("WARN","Emergency stop enabled")
    return {"ok":True}

@app.post("/api/resume")
async def resume():
    global stopped
    stopped=False;log("INFO","Bot resumed")
    return {"ok":True}

@app.get("/api/status")
async def status():return last_status

@app.get("/api/trades")
async def trades():
    with conn() as c:rows=c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/stats")
async def stats():
    with conn() as c:rows=c.execute("SELECT * FROM trades WHERE status='CLOSED'").fetchall()
    n=len(rows);wins=sum(1 for r in rows if float(r["pnl"])>0);pnl=sum(float(r["pnl"]) for r in rows)
    avg_edge=sum(float(r["edge"]) for r in rows)/n if n else 0
    return {"closed_trades":n,"wins":wins,"losses":n-wins,
            "win_rate":wins/n if n else 0,"total_pnl":pnl,"avg_entry_edge":avg_edge}

@app.get("/",response_class=HTMLResponse)
async def dashboard():return HTML

HTML=r"""<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>BTC 5M Bot v0.3</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#090c0f;color:#f7f7f8;margin:0;padding:20px}
.wrap{max-width:680px;margin:auto}.card{background:#171b20;border:1px solid #252b33;border-radius:20px;padding:18px;margin:12px 0}
h1{font-size:28px}.big{font-size:35px;font-weight:800}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.muted{color:#9ca3af}.warn{color:#fbbf24;font-size:13px}.good{color:#4ade80}.bad{color:#fb7185}
.pill{display:inline-block;border-radius:999px;background:#272d35;padding:5px 9px;font-size:12px}
.row{display:flex;justify-content:space-between;gap:12px;margin:6px 0}.small{font-size:13px}
button{border:0;border-radius:16px;padding:15px;font-weight:800;font-size:16px;width:100%}.stop{background:#ef4444;color:white}.go{background:#22c55e;color:#07140b}
.trade{font-size:13px;padding:9px 0;border-bottom:1px solid #293039}
</style></head><body><div class="wrap">
<h1>BTC 5M Bot <span class="pill">PAPER · v0.3</span></h1>
<div class="card"><div class="muted">Current market</div><div id="market">Starting…</div><div id="feed" class="warn"></div><div id="quality" class="muted small"></div></div>
<div class="card"><div class="muted">Current signal</div><div id="sig" class="big">Starting…</div><div id="why"></div><div id="blocked" class="warn"></div></div>
<div id="openCard" class="card" style="display:none"><div class="muted">Open paper position</div><div id="openSide" class="big"></div><div id="openDetails"></div></div>
<div class="grid"><div class="card"><div class="muted">Time left</div><div id="time" class="big">—</div></div><div class="card"><div class="muted">TWAP move</div><div id="move" class="big">—</div></div></div>
<div class="grid"><div class="card"><div class="muted">UP ask</div><div id="up" class="big">—</div></div><div class="card"><div class="muted">DOWN ask</div><div id="down" class="big">—</div></div></div>
<div class="card">
<div class="row"><span>Current probability</span><b id="prob">—</b></div>
<div class="row"><span>Current edge</span><b id="edge">—</b></div>
<div class="row"><span>Next stake</span><b id="stake">—</b></div>
<div class="row"><span>Bankroll</span><b id="bank">—</b></div>
<div class="row"><span>Daily P&L</span><b id="pnl">—</b></div>
</div>
<div class="grid"><button class="stop" onclick="fetch('/api/stop',{method:'POST'})">STOP</button><button class="go" onclick="fetch('/api/resume',{method:'POST'})">RESUME</button></div>
<div class="card"><b>Performance</b><div id="stats" class="muted"></div></div>
<div class="card"><b>Recent trades</b><div id="trades" class="muted"></div></div>
</div>
<script>
function money(x){return '$'+Number(x||0).toFixed(2)}
async function tick(){try{
let x=await fetch('/api/status').then(r=>r.json());
market.textContent=x.market?.slug||x.message||'Searching…';
feed.textContent=x.feed_warning||'';
quality.textContent=x.start_ref_method?`Start ref: ${x.start_ref_method} · coverage ${Math.round((x.current_quality?.coverage||0)*100)}% · sources ${(x.current_quality?.source_count||0).toFixed(1)}`:'';
if(x.signal){
 sig.textContent=x.signal.action+(x.signal.side?' '+x.signal.side:'');why.textContent=x.signal.reason||'';
 time.textContent=(x.seconds_left??0)+'s';move.textContent=(x.move_bps??0).toFixed(2)+' bp';
 up.textContent=(x.up?.ask??0).toFixed(3);down.textContent=(x.down?.ask??0).toFixed(3);
 prob.textContent=((x.signal.probability||0)*100).toFixed(1)+'%';edge.textContent=((x.signal.edge||0)*100).toFixed(1)+'%';
 stake.textContent=money(x.next_stake);bank.textContent=money(x.bankroll);pnl.textContent=money(x.daily_pnl);
 blocked.textContent=x.blocked?('BLOCKED: '+x.blocked):'';
}else{sig.textContent=x.message||'Waiting…'}
if(x.open_trade){
 openCard.style.display='block';let t=x.open_trade;
 openSide.textContent=`${t.side} · ${money(t.stake)} @ ${Number(t.price).toFixed(3)}`;
 openDetails.innerHTML=`Entry probability <b>${(Number(t.probability)*100).toFixed(1)}%</b> · edge <b>${(Number(t.edge)*100).toFixed(1)}%</b><br>Entered with <b>${t.entry_seconds_left}s</b> left · move <b>${Number(t.entry_move_bps||0).toFixed(2)} bp</b><br><span class="muted small">${t.entry_feed_quality||''}</span>`;
}else openCard.style.display='none';
let st=await fetch('/api/stats').then(r=>r.json());
stats.textContent=st.closed_trades?`${st.wins}-${st.losses} · ${(st.win_rate*100).toFixed(1)}% win rate · P&L ${money(st.total_pnl)} · avg entry edge ${(st.avg_entry_edge*100).toFixed(1)}%`:'No resolved v0.3 trades yet';
let tr=await fetch('/api/trades').then(r=>r.json());
trades.innerHTML=tr.slice(0,8).map(t=>`<div class="trade"><b>${t.side}</b> ${money(t.stake)} @ ${Number(t.price).toFixed(3)} · ${(Number(t.probability)*100).toFixed(1)}% model · ${(Number(t.edge)*100).toFixed(1)}% edge · ${t.status}${t.status==='CLOSED'?' · '+money(t.pnl):''}</div>`).join('')||'No trades yet';
}catch(e){sig.textContent='Dashboard reconnecting…'}}
setInterval(tick,1500);tick();
</script></body></html>"""
