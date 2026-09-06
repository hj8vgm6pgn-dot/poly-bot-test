from dataclasses import dataclass
from math import exp

@dataclass
class Signal:
    action: str
    side: str | None
    probability: float
    market_price: float | None
    edge: float
    reason: str
    raw_up_probability: float
    blended_up_probability: float
    market_up_probability: float
    model_market_gap: float

def logistic(x):
    return 1 / (1 + exp(-max(-12, min(12, x))))

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def estimate_raw_up_probability(start_twap, current_twap, seconds_left, recent_vol_bps):
    move_bps = (current_twap / start_twap - 1) * 10000
    vol = max(recent_vol_bps, 1.5)
    time_scale = max(seconds_left, 10) ** 0.5 / (120 ** 0.5)
    z = move_bps / (vol * max(0.45, time_scale))
    return logistic(0.55 * z)

def fair_market_up(up_ask, down_ask):
    # Convert two asks into a normalized market-implied probability.
    # This reduces the distortion caused by the bid/ask overround.
    total = up_ask + down_ask
    if total <= 0:
        return 0.5
    return clamp(up_ask / total, 0.01, 0.99)

def decide(start_twap,current_twap,seconds_left,up_ask,down_ask,
           spread_up,spread_down,liquidity_up,liquidity_down,recent_vol_bps,
           min_edge,max_entry,min_secs,max_secs,max_spread,min_liquidity,min_abs_move_bps,
           model_prob_cap=0.90, market_blend_weight=0.45, max_model_market_gap=0.30,
           min_entry_price=0.08, max_entry_price_hard=0.92,
           extreme_price_threshold=0.12, extreme_min_move_bps=6.0,
           extreme_min_seconds_left=45, min_model_probability=0.58):

    raw_up = estimate_raw_up_probability(start_twap,current_twap,seconds_left,recent_vol_bps)
    raw_up = clamp(raw_up, 1-model_prob_cap, model_prob_cap)

    market_up = fair_market_up(up_ask, down_ask)
    blended_up = (1-market_blend_weight)*raw_up + market_blend_weight*market_up
    blended_up = clamp(blended_up, 1-model_prob_cap, model_prob_cap)
    gap = abs(raw_up - market_up)

    if not (min_secs <= seconds_left <= max_secs):
        return Signal("SKIP",None,.5,None,0,"Outside trade window",
                      raw_up,blended_up,market_up,gap)

    move_bps = (current_twap/start_twap-1)*10000
    if abs(move_bps) < min_abs_move_bps:
        return Signal("SKIP",None,.5,None,0,f"TWAP move too small ({move_bps:.2f} bps)",
                      raw_up,blended_up,market_up,gap)

    if gap > max_model_market_gap:
        return Signal("SKIP",None,blended_up,None,0,
                      f"Model/market disagreement too large ({gap:.1%})",
                      raw_up,blended_up,market_up,gap)

    candidates = [
        ("UP", blended_up, raw_up, up_ask, spread_up, liquidity_up),
        ("DOWN", 1-blended_up, 1-raw_up, down_ask, spread_down, liquidity_down),
    ]
    side,prob,raw_side_prob,px,spr,liq = max(candidates,key=lambda x:x[1]-x[3])
    edge = prob-px

    if raw_side_prob < min_model_probability:
        return Signal("SKIP",side,prob,px,edge,
                      f"Raw model confidence too low ({raw_side_prob:.1%})",
                      raw_up,blended_up,market_up,gap)

    if spr > max_spread:
        return Signal("SKIP",side,prob,px,edge,f"Spread too wide ({spr:.3f})",
                      raw_up,blended_up,market_up,gap)

    if liq < min_liquidity:
        return Signal("SKIP",side,prob,px,edge,f"Liquidity too low (${liq:.0f})",
                      raw_up,blended_up,market_up,gap)

    if px < min_entry_price or px > max_entry_price_hard:
        return Signal("SKIP",side,prob,px,edge,
                      f"Extreme market price blocked ({px:.3f})",
                      raw_up,blended_up,market_up,gap)

    # Extra protection around penny/lottery prices.
    if px <= extreme_price_threshold:
        if abs(move_bps) < extreme_min_move_bps or seconds_left < extreme_min_seconds_left:
            return Signal("SKIP",side,prob,px,edge,
                          "Extreme-price setup lacks enough move/time confirmation",
                          raw_up,blended_up,market_up,gap)

    if px > max_entry:
        return Signal("SKIP",side,prob,px,edge,f"Entry price too high ({px:.3f})",
                      raw_up,blended_up,market_up,gap)

    if edge < min_edge:
        return Signal("SKIP",side,prob,px,edge,f"Blended edge too small ({edge:.1%})",
                      raw_up,blended_up,market_up,gap)

    return Signal("BUY",side,prob,px,edge,"All v0.4 filters passed",
                  raw_up,blended_up,market_up,gap)
