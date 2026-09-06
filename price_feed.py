import time, statistics, httpx
from db import conn

class MultiProxyFeed:
    """
    Paper-mode approximation, not Chainlink.
    Pulls multiple public BTC/USD spot sources, takes a median composite,
    persists every observation, and can calculate 60-second TWAP at an exact timestamp.
    """
    def __init__(self):
        self.source="Median composite: Coinbase + Kraken + Bitstamp"
        self.last_sources={}
        self.last_error=None

    async def _coinbase(self, client):
        r=await client.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                           headers={"User-Agent":"btc5m-paper-bot/0.3"})
        r.raise_for_status()
        return float(r.json()["price"])

    async def _kraken(self, client):
        r=await client.get("https://api.kraken.com/0/public/Ticker",params={"pair":"XBTUSD"})
        r.raise_for_status()
        data=r.json()["result"]
        row=next(iter(data.values()))
        return float(row["c"][0])

    async def _bitstamp(self, client):
        r=await client.get("https://www.bitstamp.net/api/v2/ticker/btcusd/")
        r.raise_for_status()
        return float(r.json()["last"])

    async def update(self):
        import asyncio
        async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
            names=["coinbase","kraken","bitstamp"]
            results=await asyncio.gather(
                self._coinbase(client),self._kraken(client),self._bitstamp(client),
                return_exceptions=True
            )
        vals={}
        for n,r in zip(names,results):
            if not isinstance(r,Exception):
                vals[n]=float(r)
        if not vals:
            raise RuntimeError("All proxy price sources failed")
        px=float(statistics.median(vals.values()))
        now=time.time()
        self.last_sources=vals
        with conn() as c:
            c.execute("INSERT INTO price_obs(ts,price,source,source_count) VALUES(?,?,?,?)",
                      (now,px,"multi_proxy",len(vals)))
            c.execute("DELETE FROM price_obs WHERE ts<?",(now-86400,))
        return px

    def _rows(self, start_ts, end_ts):
        with conn() as c:
            rows=c.execute("""SELECT ts,price,source_count FROM price_obs
                              WHERE ts>=? AND ts<=? ORDER BY ts""",
                           (start_ts-2,end_ts+2)).fetchall()
        return [(float(r["ts"]),float(r["price"]),int(r["source_count"] or 1)) for r in rows]

    def twap_at(self, end_ts, seconds=60):
        start=end_ts-seconds
        pts=self._rows(start,end_ts)
        if not pts:return None,None

        # Use nearest first observation as start approximation.
        pts=[p for p in pts if p[0] <= end_ts]
        if not pts:return None,None
        # If first sample is after requested start, quality reflects that gap.
        first_gap=max(0.0,pts[0][0]-start)
        weighted=0.0
        duration=0.0
        prev_t=max(start,pts[0][0])
        prev_p=pts[0][1]
        for t,p,_ in pts[1:]:
            if t>end_ts:break
            dt=max(0.0,t-prev_t)
            weighted+=prev_p*dt; duration+=dt
            prev_t=t; prev_p=p
        dt=max(0.0,end_ts-prev_t)
        weighted+=prev_p*dt; duration+=dt
        if duration<=0:return pts[-1][1],{"coverage":0,"source_count":pts[-1][2]}
        coverage=min(1.0,duration/seconds)
        avg_sources=sum(p[2] for p in pts)/len(pts)
        quality={"coverage":coverage,"first_gap":first_gap,"source_count":avg_sources,
                 "samples":len(pts)}
        return weighted/duration,quality

    def recent_vol_bps(self, seconds=45):
        now=time.time()
        pts=self._rows(now-seconds,now)
        prices=[p for _,p,_ in pts]
        if len(prices)<5:return 3.0
        rets=[(prices[i]/prices[i-1]-1)*10000 for i in range(1,len(prices))]
        if len(rets)<2:return 3.0
        return max(1.0,statistics.pstdev(rets)*3)
