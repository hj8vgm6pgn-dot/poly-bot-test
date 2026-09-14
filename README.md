# BTC 5-Minute Polymarket Bot v0.5

v0.5 changes the research goal from static model-vs-market disagreement to short-term lag detection.

New:
- 5s / 10s / 20s BTC momentum
- BTC acceleration
- exchange disagreement
- Polymarket order-book imbalance
- contract-price velocity
- directional lag score
- filter pass counter
- exact-boundary requirement before paper trades
- version-separated v0.5 statistics

Still PAPER ONLY.
The BTC feed remains a multi-exchange proxy, not the exact Chainlink settlement feed.
Live execution remains disabled.


## v0.5.1 monitoring patch

New read-only monitoring endpoints:

- `/api/public` — combined current bot state, v0.5 performance, and 10 most recent trades.
- `/api/healthz` — lightweight health check.

These endpoints do not place orders and do not expose wallet secrets. They are for remote monitoring without relying on the browser dashboard's JavaScript rendering.


## v0.5.2 monitoring compatibility patch

The existing dashboard URL `/` now contains a server-rendered **Remote monitor snapshot**
with the current bot state as JSON. This makes the live state readable even when a remote
reader cannot execute the dashboard JavaScript or access a separate API route.

No strategy thresholds, candidate logic, or paper-trading behavior were changed.


## v0.5.3 monitoring field-map fix

The server-rendered remote snapshot now flattens the real nested live state from
`signal`, `momentum`, `up`, `down`, `current_quality`, and `market`.

This exposes live fields including lag score/direction, TWAP move, UP/DOWN prices,
5s/10s/20s momentum, filters passed, reason, edge, bankroll, and source quality.

No strategy logic, thresholds, position sizing, or paper-trading behavior changed.


## v0.5.4 no-cache remote monitoring

Adds `/monitor.txt`, a fresh plain-text JSON snapshot of the current bot state.

The endpoint sends:
- `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`
- `Pragma: no-cache`
- `Expires: 0`

The main `/` dashboard is also returned with no-cache headers and includes a normal
link to `/monitor.txt` so remote readers can discover and follow it from the dashboard.

No strategy logic, thresholds, sizing, or paper-trading behavior changed.


## v0.6 research strategy

- PAPER ONLY; live execution remains disabled.
- Entry observation window widened from 30–120s to 30–180s.
- Strict lag threshold remains 0.58.
- Candidate direction is now determined by the model first; the bot no longer
  chooses the opposite contract simply because it is cheap.
- Model/lag disagreement is explicitly rejected as `Direction mismatch`.
- Extreme contracts already priced at >= 0.92 receive a clearer
  `Market already priced` rejection unless lag evidence is strong.
- `/monitor.txt` adds a `shadow_setup` diagnostic for skipped snapshots with
  lag score 0.30–0.58. Shadow setups DO NOT place paper trades and do not alter
  bankroll/performance.
- This version is intended to collect more useful research observations without
  weakening the strict trading threshold.

Before relying on multi-day/week calibration data, mount a persistent Railway
Volume at `/data` so `/data/bot.db` survives redeployments.


## v0.7 maker-only paper execution

This branch keeps the v0.6 prediction thresholds frozen and changes execution only.

- PAPER ONLY.
- Qualifying BUY signals create a resting maker order instead of filling at the ask.
- Limit price starts at best bid, improved by one cent only when it remains strictly below the ask.
- There is no taker fallback.
- A paper maker order is not considered filled merely because it rests at the bid.
- Conservative fill rule: a later observed best ask must move down to or through the resting limit.
- Unfilled orders expire when the entry window closes or the market changes.
- Maker orders have their own lifecycle table and fill-rate statistics.
- Filled maker trades are tagged `strategy_version=0.7-maker` and `execution_type=MAKER`, keeping v0.6 history separate.

This first implementation intentionally avoids optimistic queue-position assumptions. Partial-fill and queue-depth modelling can be added after we observe real maker fill behavior.
