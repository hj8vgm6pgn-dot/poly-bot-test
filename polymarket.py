import json, httpx, time

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

def _loads(v):
    if isinstance(v, list): return v
    if not v: return []
    try: return json.loads(v)
    except Exception: return []

async def event_by_slug(slug):
    async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
        r = await client.get(f"{GAMMA}/events/slug/{slug}")
        if r.status_code == 404: return None
        r.raise_for_status()
        return r.json()

async def discover_current_btc5m(now=None):
    now = int(now or time.time())
    base = (now // 300) * 300
    for start_ts in (base, base+300, base-300):
        slug=f"btc-updown-5m-{start_ts}"
        try: ev=await event_by_slug(slug)
        except Exception: continue
        if not ev: continue
        markets=ev.get("markets") or []
        if not markets: continue
        m=markets[0]
        outcomes=[str(x).upper() for x in _loads(m.get("outcomes"))]
        tokens=_loads(m.get("clobTokenIds"))
        if len(outcomes)<2 or len(tokens)<2: continue
        mapping=dict(zip(outcomes,tokens))
        up=mapping.get("UP") or mapping.get("YES")
        down=mapping.get("DOWN") or mapping.get("NO")
        if not up or not down: continue
        if start_ts <= now < start_ts+300:
            return {
                "market_id":str(m.get("conditionId") or m.get("id") or slug),
                "slug":slug,"title":ev.get("title") or "BTC Up or Down 5m",
                "start_ts":start_ts,"end_ts":start_ts+300,
                "up_token_id":str(up),"down_token_id":str(down)
            }
    return None

async def get_book(token_id):
    async with httpx.AsyncClient(timeout=5) as client:
        r=await client.get(f"{CLOB}/book",params={"token_id":token_id})
        r.raise_for_status()
        return r.json()

def book_metrics(book):
    asks=sorted((float(x["price"]),float(x["size"])) for x in book.get("asks",[]))
    bids=sorted(((float(x["price"]),float(x["size"])) for x in book.get("bids",[])),reverse=True)
    ask=asks[0][0] if asks else 1.0
    bid=bids[0][0] if bids else 0.0
    spread=max(0,ask-bid)
    liq=sum(px*sz for px,sz in asks if px<=ask+0.05)
    return ask,bid,spread,liq

async def resolved_winner(slug):
    ev=await event_by_slug(slug)
    if not ev:return None
    markets=ev.get("markets") or []
    if not markets:return None
    m=markets[0]
    if not m.get("closed"):return None
    outcomes=[str(x).upper() for x in _loads(m.get("outcomes"))]
    prices=_loads(m.get("outcomePrices"))
    if len(outcomes)!=len(prices) or not prices:return None
    vals=[float(x) for x in prices]
    i=max(range(len(vals)),key=lambda j:vals[j])
    if vals[i]<.95:return None
    o=outcomes[i]
    if o in ("UP","YES"):return "UP"
    if o in ("DOWN","NO"):return "DOWN"
    return None
