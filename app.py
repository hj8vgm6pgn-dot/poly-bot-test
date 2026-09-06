import os,time,statistics
from collections import deque
from dotenv import load_dotenv
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from db import init_db,conn
from strategy import decide
from polymarket import get_book,book_metrics

load_dotenv()
init_db()
app=FastAPI(title="BTC 5m Polymarket Bot")

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

market={}
twaps=deque(maxlen=120)
stopped=False

class TwapIn(BaseModel):
    price: float
    timestamp: int|None=None

class MarketIn(BaseModel):
    market_id:str
    up_token_id:str
    down_token_id:str
    start_ts:int
    end_ts:int
    start_twap:float

def pnl_today():
    cutoff=int(time.time())-86400
    with conn() as c:
        r=c.execute("SELECT COALESCE(SUM(pnl),0) p FROM trades WHERE ts>=?",(cutoff,)).fetchone()
    return float(r["p"])

def already_traded(mid):
    with conn() as c:
        return c.execute("SELECT 1 FROM trades WHERE market_id=? LIMIT 1",(mid,)).fetchone() is not None

def vol_bps():
    if len(twaps)<5:return 3.0
    p=[x[1] for x in twaps]
    r=[(p[i]/p[i-1]-1)*10000 for i in range(1,len(p))]
    return max(1.0,statistics.pstdev(r)*3)

@app.post("/api/market")
async def set_market(m:MarketIn):
    global market
    market=m.model_dump()
    return {"ok":True}

@app.post("/api/twap")
async def add_twap(t:TwapIn):
    ts=t.timestamp or int(time.time())
    twaps.append((ts,t.price))
    with conn() as c:c.execute("INSERT OR REPLACE INTO twap VALUES (?,?)",(ts,t.price))
    return {"ok":True}

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
async def status():
    now=int(time.time())
    if not market:return {"mode":MODE,"message":"No market configured","stopped":stopped}
    if not twaps:return {"mode":MODE,"message":"Waiting for TWAP","stopped":stopped}

    seconds_left=market["end_ts"]-now
    current=twaps[-1][1]
    try:
        ub=await get_book(market["up_token_id"])
        db=await get_book(market["down_token_id"])
        ua,ubid,us,ul=book_metrics(ub)
        da,dbid,ds,dl=book_metrics(db)
    except Exception as e:
        return {"mode":MODE,"message":f"Order-book error: {e}","stopped":stopped}

    sig=decide(market["start_twap"],current,seconds_left,ua,da,us,ds,ul,dl,
               vol_bps(),MIN_EDGE,MAX_ENTRY,MIN_SECS,MAX_SECS,MAX_SPREAD,MIN_LIQ,MIN_MOVE)

    pnl=pnl_today()
    bankroll=START+pnl
    stake=max(1,bankroll*RISK)
    blocked=None
    if stopped:blocked="Emergency stop active"
    elif pnl<=-(START*MAX_DAILY):blocked="Daily loss limit reached"
    elif already_traded(market["market_id"]):blocked="Already traded this market"

    executed=False
    if sig.action=="BUY" and not blocked:
        if MODE=="paper":
            shares=stake/sig.market_price
            with conn() as c:
                c.execute("""INSERT INTO trades(ts,market_id,side,price,stake,shares,probability,edge,mode)
                             VALUES(?,?,?,?,?,?,?,?,?)""",
                          (now,market["market_id"],sig.side,sig.market_price,stake,shares,
                           sig.probability,sig.edge,MODE))
            executed=True
        else:
            blocked="Live execution disabled in v0.1"

    return {
        "mode":MODE,"stopped":stopped,"seconds_left":seconds_left,
        "move_bps":(current/market["start_twap"]-1)*10000,
        "up":{"ask":ua,"bid":ubid,"spread":us,"liquidity":ul},
        "down":{"ask":da,"bid":dbid,"spread":ds,"liquidity":dl},
        "signal":sig.__dict__,"blocked":blocked,"paper_trade_executed":executed,
        "daily_pnl":pnl,"bankroll":bankroll,"next_stake":stake
    }

@app.post("/api/resolve/{market_id}/{winner}")
async def resolve(market_id:str,winner:str):
    winner=winner.upper()
    if winner not in ("UP","DOWN"):raise HTTPException(400,"winner must be UP or DOWN")
    with conn() as c:
        rows=c.execute("SELECT * FROM trades WHERE market_id=? AND status='OPEN'",(market_id,)).fetchall()
        for r in rows:
            pnl=(r["shares"]-r["stake"]) if r["side"]==winner else -r["stake"]
            c.execute("UPDATE trades SET status='CLOSED',pnl=? WHERE id=?",(pnl,r["id"]))
    return {"ok":True}

@app.get("/api/trades")
async def trades():
    with conn() as c:rows=c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/",response_class=HTMLResponse)
async def dashboard():
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<style>body{font-family:-apple-system;background:#0b0d10;color:white;padding:18px}.card{background:#171a1f;border-radius:18px;padding:18px;margin:12px 0}.big{font-size:34px;font-weight:800}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}button{width:100%;padding:15px;border:0;border-radius:14px;font-weight:800}.stop{background:#ef4444;color:white}.go{background:#22c55e}.muted{color:#9ca3af}</style>
</head><body><h2>BTC 5M Bot</h2><div class='card'><div class='muted'>Signal</div><div id='sig' class='big'>Loading</div><div id='why'></div></div>
<div class='grid'><div class='card'><div class='muted'>Time</div><div id='t' class='big'>—</div></div><div class='card'><div class='muted'>TWAP move</div><div id='m' class='big'>—</div></div></div>
<div class='grid'><div class='card'>UP ask<div id='u' class='big'>—</div></div><div class='card'>DOWN ask<div id='d' class='big'>—</div></div></div>
<div class='card'><div>Probability <b id='p'>—</b></div><div>Edge <b id='e'>—</b></div><div>Stake <b id='s'>—</b></div><div>Bankroll <b id='b'>—</b></div><div id='blocked' class='muted'></div></div>
<div class='grid'><button class='stop' onclick="fetch('/api/stop',{method:'POST'})">STOP</button><button class='go' onclick="fetch('/api/resume',{method:'POST'})">RESUME</button></div>
<script>async function tick(){let x=await fetch('/api/status').then(r=>r.json());if(!x.signal){sig.textContent=x.message||'Waiting';return}sig.textContent=x.signal.action+' '+(x.signal.side||'');why.textContent=x.signal.reason;t.textContent=x.seconds_left+'s';m.textContent=x.move_bps.toFixed(2)+' bp';u.textContent=x.up.ask.toFixed(2);d.textContent=x.down.ask.toFixed(2);p.textContent=(x.signal.probability*100).toFixed(1)+'%';e.textContent=(x.signal.edge*100).toFixed(1)+'%';s.textContent='$'+x.next_stake.toFixed(2);b.textContent='$'+x.bankroll.toFixed(2);blocked.textContent=x.blocked||''}setInterval(tick,1500);tick()</script></body></html>"""
