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

def logistic(x):
    return 1 / (1 + exp(-max(-12, min(12, x))))

def estimate_up_probability(start_twap, current_twap, seconds_left, recent_vol_bps):
    move_bps = (current_twap / start_twap - 1) * 10000
    vol = max(recent_vol_bps, 1.0)
    time_scale = max(seconds_left, 10) ** 0.5 / (120 ** 0.5)
    z = move_bps / (vol * max(0.35, time_scale))
    return logistic(0.85 * z)

def decide(start_twap,current_twap,seconds_left,up_ask,down_ask,
           spread_up,spread_down,liquidity_up,liquidity_down,recent_vol_bps,
           min_edge,max_entry,min_secs,max_secs,max_spread,min_liquidity,min_abs_move_bps):

    if not (min_secs <= seconds_left <= max_secs):
        return Signal("SKIP",None,.5,None,0,"Outside trade window")

    move_bps = (current_twap/start_twap-1)*10000
    if abs(move_bps) < min_abs_move_bps:
        return Signal("SKIP",None,.5,None,0,f"TWAP move too small ({move_bps:.2f} bps)")

    p_up = estimate_up_probability(start_twap,current_twap,seconds_left,recent_vol_bps)
    candidates = [
        ("UP",p_up,up_ask,spread_up,liquidity_up),
        ("DOWN",1-p_up,down_ask,spread_down,liquidity_down),
    ]
    side,prob,px,spr,liq = max(candidates,key=lambda x:x[1]-x[2])
    edge = prob-px

    if spr > max_spread:
        return Signal("SKIP",side,prob,px,edge,f"Spread too wide ({spr:.3f})")
    if liq < min_liquidity:
        return Signal("SKIP",side,prob,px,edge,f"Liquidity too low (${liq:.0f})")
    if px > max_entry:
        return Signal("SKIP",side,prob,px,edge,f"Entry price too high ({px:.2f})")
    if edge < min_edge:
        return Signal("SKIP",side,prob,px,edge,f"Edge too small ({edge:.1%})")
    return Signal("BUY",side,prob,px,edge,"All filters passed")
