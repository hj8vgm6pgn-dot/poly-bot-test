# BTC 5-Minute Polymarket Bot v0.3

Paper-only accuracy and diagnostics update.

## What changed from v0.2

- Uses a median BTC/USD composite from Coinbase, Kraken and Bitstamp rather than one exchange.
- Persists price observations in SQLite.
- Calculates a 60-second TWAP ending at the exact 5-minute market boundary.
- Persists each market's starting reference so Railway restarts do not silently move the reference.
- Stores the complete entry snapshot:
  - entry price
  - model probability
  - model edge
  - starting TWAP
  - entry TWAP
  - TWAP movement in bps
  - seconds remaining
  - spread
  - liquidity
  - feed-quality description
- Dashboard separates the current signal from the open paper position.
- Automatic resolution and paper P&L remain enabled.
- Live execution remains disabled.

## Important limitation

Polymarket's official BTC 5-minute rules use the Chainlink BTC/USD 60-second TWAP.
This build still does NOT have direct authenticated access to that exact stream.

The multi-exchange composite is intended to diagnose and paper-test the strategy more honestly.
Do not use v0.3 for live money.

## Upgrade your existing Railway deployment

Upload/replace these files in your current GitHub repo:

- app.py
- db.py
- strategy.py
- polymarket.py
- price_feed.py
- live_executor.py
- requirements.txt
- railway.toml
- README.md

Commit/merge into `main`. Railway should redeploy automatically.

Your existing database will migrate automatically; old paper trades are retained, but their
new v0.3 diagnostic fields will be blank. Evaluate v0.3 trades separately when reviewing results.

Recommended Railway variables:
- BOT_MODE=paper
- STARTING_BANKROLL=1000
- RISK_PER_TRADE_PCT=1
- MAX_DAILY_LOSS_PCT=5
- MIN_EDGE=0.08
- MAX_ENTRY_PRICE=0.72
- MIN_SECONDS_LEFT=30
- MAX_SECONDS_LEFT=120
- MAX_SPREAD=0.05
- MIN_BOOK_LIQUIDITY_USD=50
- MIN_ABS_TWAP_MOVE_BPS=2
- BOT_POLL_SECONDS=1

Never commit wallet keys or seed phrases to GitHub.
