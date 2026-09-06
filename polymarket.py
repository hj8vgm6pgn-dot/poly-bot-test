import httpx
CLOB="https://clob.polymarket.com"

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
