# BTC 5-Minute Polymarket Bot v0.4

v0.4 is a paper-only signal-quality upgrade.

## Main changes
- Caps raw model probability at 90%.
- Blends model probability with Polymarket's implied probability.
- Blocks trades when model and market disagree by more than 30 percentage points.
- Blocks extreme 1c/99c-style entries by default.
- Requires stronger confirmation for very cheap contracts.
- Requires a minimum raw model confidence.
- Logs eligible signals every ~5 seconds for future calibration.
- Adds calibration endpoint: `/api/calibration`.
- Stores raw model probability, market-implied probability, blended probability, and model/market gap on every trade.
- Keeps the exact-boundary multi-exchange proxy from v0.3.
- Keeps live trading disabled.

## Why
v0.3 correctly exposed that the probability model was overconfident and could buy contracts at 1c purely because the model disagreed sharply with the market. v0.4 treats extreme disagreement as a reason to skip, not a reason to bet.

## Upgrade
Replace the current repo files with this package and merge to `main`. Railway will redeploy automatically.

Keep:
- BOT_MODE=paper
- existing Railway domain/networking
- no wallet keys in GitHub

## Important
The price feed remains a multi-exchange proxy and is NOT the exact Chainlink settlement feed. v0.4 is for paper testing and calibration only.
