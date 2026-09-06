# BTC 5-Minute Polymarket Bot v0.1

This is a paper-trading-first prototype with an iPhone-friendly web dashboard.

## Included
- Polymarket CLOB order-book reader
- BTC 5m decision engine
- 120s to 30s trade window
- minimum edge filter
- maximum entry-price filter
- spread/liquidity filters
- one trade per market
- bankroll-based sizing
- daily loss stop
- emergency STOP/RESUME
- SQLite trade log
- mobile dashboard

## Not yet enabled
Live order placement is intentionally disabled in v0.1.
Automatic Chainlink Data Streams ingestion and automatic 5m market discovery are the next integration steps.

## Run
1. Install Python 3.11+
2. `python -m venv .venv`
3. Activate the venv
4. `pip install -r requirements.txt`
5. Copy `.env.example` to `.env`
6. `python run.py`
7. Open `http://127.0.0.1:8000`

## Market input
POST `/api/market` with:
`market_id`, `up_token_id`, `down_token_id`, `start_ts`, `end_ts`, `start_twap`.

## TWAP input
POST `/api/twap` with:
`price` and optional Unix `timestamp`.

Important: current 5-minute BTC Polymarket resolution uses Chainlink BTC/USD 60-second TWAP, so a spot-only feed is not sufficient for final production use.

Never paste a wallet seed phrase or private key into chat.
