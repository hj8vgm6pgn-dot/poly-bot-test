import time, httpx
from collections import deque

class ProxyTwapFeed:
    """
    Paper-testing proxy only.
    Samples Coinbase BTC-USD spot and computes a time-weighted average
    over the most recent ~60 seconds. This is NOT Chainlink's settlement feed.
    """
    def __init__(self):
        self.obs = deque(maxlen=240)
        self.source = "Coinbase proxy 60s TWAP"

    async def update(self):
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                                 headers={"User-Agent":"btc5m-paper-bot/0.2"})
            r.raise_for_status()
            px = float(r.json()["price"])
        now = time.time()
        self.obs.append((now, px))
        while self.obs and now - self.obs[0][0] > 75:
            self.obs.popleft()
        return px

    def twap(self, seconds=60):
        if not self.obs:
            return None
        now = self.obs[-1][0]
        pts = [(t,p) for t,p in self.obs if now-t <= seconds]
        if len(pts) == 1:
            return pts[0][1]

        # Piecewise-constant time weighting.
        weighted = 0.0
        duration = 0.0
        window_start = max(now-seconds, pts[0][0])
        prev_t = window_start
        prev_p = pts[0][1]
        for t,p in pts[1:]:
            dt = max(0.0, t-prev_t)
            weighted += prev_p*dt
            duration += dt
            prev_t,prev_p=t,p
        dt=max(0.0,now-prev_t)
        weighted += prev_p*dt
        duration += dt
        return weighted/duration if duration else pts[-1][1]

    def recent_vol_bps(self):
        pts=list(self.obs)
        if len(pts)<5:
            return 3.0
        prices=[p for _,p in pts[-30:]]
        rets=[(prices[i]/prices[i-1]-1)*10000 for i in range(1,len(prices))]
        if len(rets)<2:
            return 3.0
        mean=sum(rets)/len(rets)
        var=sum((x-mean)**2 for x in rets)/len(rets)
        return max(1.0,(var**0.5)*3)
