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
