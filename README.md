# BTC 5-Minute Polymarket Bot v0.2

v0.2 automatically discovers the current Polymarket BTC Up/Down 5-minute market,
reads its CLOB order books, builds a proxy 60-second BTC TWAP, paper-trades the
configured strategy, and later scores the trade using Polymarket's actual resolved outcome.

## New in v0.2

- Automatic market discovery using the predictable `btc-updown-5m-{unix_start}` slug.
- Automatic UP/DOWN token extraction through Polymarket Gamma API.
- Background process runs continuously on Railway.
- Automatic CLOB order-book monitoring.
- Proxy 60-second TWAP built from Coinbase BTC-USD spot samples.
- Automatic paper entries.
- Automatic result settlement from Polymarket after the market closes.
- Win rate and P&L dashboard.
- Emergency stop/resume.
- `railway.toml` included so Railway uses `$PORT` automatically.

## Important limitation

The proxy TWAP is **NOT the Chainlink BTC/USD 60-second TWAP that Polymarket uses
for settlement**. v0.2 is for paper testing and data collection only.

Before live trading, replace the proxy feed with authenticated Chainlink Data Streams
and validate the signal against a sufficiently large sample.

## Railway upgrade

Upload/replace all files in your existing GitHub repo. Railway should redeploy automatically.

Keep the existing public domain. `railway.toml` sets:

`uvicorn app:app --host 0.0.0.0 --port $PORT`

Recommended Railway variables:

- `BOT_MODE=paper`
- `STARTING_BANKROLL=1000`
- `RISK_PER_TRADE_PCT=1`
- `MAX_DAILY_LOSS_PCT=5`
- `MIN_EDGE=0.08`
- `MAX_ENTRY_PRICE=0.72`
- `MIN_SECONDS_LEFT=30`
- `MAX_SECONDS_LEFT=120`
- `MAX_SPREAD=0.05`
- `MIN_BOOK_LIQUIDITY_USD=50`
- `MIN_ABS_TWAP_MOVE_BPS=2`
- `PRICE_FEED_MODE=proxy`
- `BOT_POLL_SECONDS=2`

Never put a seed phrase or wallet private key in GitHub.
