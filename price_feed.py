import time, statistics, httpx
from db import conn

class MultiProxyFeed:
    def __init__(self):
        self.last_sources={}
        self.last_disagreement_bps=0.0

    async def _coinbase(self, client):
        r=await client.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                           headers={"User-Agent":"btc5m-paper-bot/0.5"})
        r.raise_for_status()
        return float(r.json()["price"])

    async def _kraken(self, client):
        r=await client.get("https://api.kraken.com/0/public/Ticker",params={"pair":"XBTUSD"})
        r.raise_for_status()
        row=next(iter(r.json()["result"].values()))
        return float(row["c"][0])

    async def _bitstamp(self, client):
        r=await client.get("https://www.bitstamp.net/api/v2/ticker/btcusd/")
        r.raise_for_status()
        return float(r.json()["last"])

    async def update(self):
        import asyncio
        async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
            names=["coinbase","kraken","bitstamp"]
            results=await asyncio.gather(self._coinbase(client),self._kraken(client),
                                         self._bitstamp(client),return_exceptions=True)
        vals={n:float(r) for n,r in zip(names,results) if not isinstance(r,Exception)}
        if not vals:
            raise RuntimeError("All proxy sources failed")
        px=float(statistics.median(vals.values()))
        vv=list(vals.values())
        disagreement=((max(vv)-min(vv))/px*10000) if len(vv)>=2 else 0.0
        self.last_sources=vals
        self.last_disagreement_bps=disagreement
        now=time.time()
        with conn() as c:
            c.execute("""INSERT INTO price_obs(ts,price,source,source_count,source_disagreement_bps)
                         VALUES(?,?,?,?,?)""",
                      (now,px,"multi_proxy",len(vals),disagreement))
            c.execute("DELETE FROM price_obs WHERE ts<?",(now-86400,))
        return px

    def _rows(self,start_ts,end_ts):
        with conn() as c:
            rows=c.execute("""SELECT ts,price,source_count,source_disagreement_bps
                              FROM price_obs WHERE ts>=? AND ts<=? ORDER BY ts""",
                           (start_ts-2,end_ts+2)).fetchall()
        return [(float(r["ts"]),float(r["price"]),int(r["source_count"] or 1),
                 float(r["source_disagreement_bps"] or 0)) for r in rows]

    def twap_at(self,end_ts,seconds=60):
        start=end_ts-seconds
        pts=[p for p in self._rows(start,end_ts) if p[0]<=end_ts]
        if not pts:
            return None,None
        first_gap=max(0.0,pts[0][0]-start)
        weighted=duration=0.0
        prev_t=max(start,pts[0][0]); prev_p=pts[0][1]
        for t,p,_,_ in pts[1:]:
            if t>end_ts: break
            dt=max(0.0,t-prev_t)
            weighted+=prev_p*dt
            duration+=dt
            prev_t=t; prev_p=p
        dt=max(0.0,end_ts-prev_t)
        weighted+=prev_p*dt
        duration+=dt
        if duration<=0:
            return pts[-1][1],{"coverage":0,"source_count":pts[-1][2],
                              "samples":1,"disagreement_bps":pts[-1][3]}
        return weighted/duration,{
            "coverage":min(1.0,duration/seconds),
            "first_gap":first_gap,
            "source_count":sum(p[2] for p in pts)/len(pts),
            "samples":len(pts),
            "disagreement_bps":sum(p[3] for p in pts)/len(pts)
        }

    def price_at_or_before(self, ts, lookback=5):
        with conn() as c:
            r=c.execute("""SELECT ts,price FROM price_obs
                           WHERE ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1""",
                        (ts,ts-lookback)).fetchone()
        return (float(r["price"]),float(r["ts"])) if r else (None,None)

    def momentum_bps(self, seconds):
        now=time.time()
        p_now,_=self.price_at_or_before(now,2)
        p_old,_=self.price_at_or_before(now-seconds,3)
        if not p_now or not p_old:
            return 0.0
        return (p_now/p_old-1)*10000

    def momentum_pack(self):
        m5=self.momentum_bps(5)
        m10=self.momentum_bps(10)
        m20=self.momentum_bps(20)
        accel=m5-(m20/4.0)
        return {"m5":m5,"m10":m10,"m20":m20,"acceleration":accel}
