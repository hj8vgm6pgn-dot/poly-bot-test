from dataclasses import dataclass
from math import exp

@dataclass
class Signal:
    action:str
    side:str|None
    probability:float
    market_price:float|None
    edge:float
    reason:str
    raw_up_probability:float
    blended_up_probability:float
    market_up_probability:float
    model_market_gap:float
    lag_score:float
    lag_direction:str
    filters_passed:int
    filters_total:int

def clamp(x,lo,hi): return max(lo,min(hi,x))
def logistic(x):
    x=max(-12,min(12,x))
    return 1/(1+exp(-x))

def market_up_probability(up_ask,down_ask):
    t=up_ask+down_ask
    return clamp(up_ask/t if t>0 else .5,.01,.99)

def raw_probability(move_bps,seconds_left,m5,m10,m20,accel,imb):
    tf=max(.35,(seconds_left/120)**.5)
    directional=.55*move_bps+.30*m10+.20*m20+.25*accel+2.0*imb
    return logistic(directional/(3.5*tf))

def _norm(x,scale): return clamp(x/scale,-1,1)

def lag_score(m5,m10,m20,accel,imb,contract_velocity):
    spot=.50*_norm(m5,3)+.30*_norm(m10,5)+.20*_norm(m20,8)
    acc=_norm(accel,2.5)
    book=clamp(imb,-1,1)
    vel=_norm(contract_velocity,.08)
    directional=.35*spot+.20*acc+.20*book+.25*vel
    direction="UP" if directional>=0 else "DOWN"
    strength=abs(directional)
    underlying=abs(.35*spot+.20*acc+.20*book)
    contract_follow=abs(.25*vel)
    lag_component=clamp(underlying-.35*contract_follow,0,1)
    return clamp(.55*strength+.45*lag_component,0,1),direction

def decide(*,start_twap,current_twap,seconds_left,up_ask,down_ask,
           spread_up,spread_down,liq_up,liq_down,m5,m10,m20,accel,
           book_imbalance,contract_velocity,source_count,source_disagreement_bps,
           min_edge,max_entry,min_entry,min_secs,max_secs,max_spread,min_liq,
           min_abs_move,min_model_prob,model_prob_cap,market_blend_weight,
           max_model_market_gap,lag_min_score,lag_strong_score,
           max_source_disagreement_bps,min_active_sources,
           extreme_price_threshold,extreme_min_move_bps,extreme_min_seconds_left):

    move_bps=(current_twap/start_twap-1)*10000
    mkt_up=market_up_probability(up_ask,down_ask)
    raw_up=clamp(raw_probability(move_bps,seconds_left,m5,m10,m20,accel,book_imbalance),
                 1-model_prob_cap,model_prob_cap)
    lag,lag_dir=lag_score(m5,m10,m20,accel,book_imbalance,contract_velocity)
    blend_up=clamp((1-market_blend_weight)*raw_up+market_blend_weight*mkt_up,
                   1-model_prob_cap,model_prob_cap)
    gap=abs(raw_up-mkt_up)

    # v0.6: determine model direction FIRST. Never pick the opposite side merely
    # because its contract is cheap and therefore produces a large arithmetic edge.
    model_dir="UP" if raw_up>=.5 else "DOWN"
    side=model_dir

    if side=="UP":
        prob,raw_side,px,spr,liq=blend_up,raw_up,up_ask,spread_up,liq_up
    else:
        prob,raw_side,px,spr,liq=1-blend_up,1-raw_up,down_ask,spread_down,liq_down
    edge=prob-px

    checks=[
        (min_secs<=seconds_left<=max_secs,"trade window"),
        (source_count>=min_active_sources,"source count"),
        (source_disagreement_bps<=max_source_disagreement_bps,"source agreement"),
        (abs(move_bps)>=min_abs_move,"TWAP move"),
        (spr<=max_spread,"spread"),
        (liq>=min_liq,"liquidity"),
        (min_entry<=px<=max_entry,"entry price"),
        (raw_side>=min_model_prob,"model confidence"),
        (edge>=min_edge,"edge"),
        (lag>=lag_min_score,"lag score"),
        (side==lag_dir,"direction match")
    ]
    passed=sum(1 for ok,_ in checks if ok)

    # Clearer primary rejection reasons.
    if not checks[0][0]: reason="Outside trade window"
    elif not checks[1][0]: reason=f"Not enough active sources ({source_count:.1f})"
    elif not checks[2][0]: reason=f"Exchange disagreement too high ({source_disagreement_bps:.2f} bps)"
    elif not checks[3][0]: reason=f"TWAP move too small ({move_bps:.2f} bps)"
    elif side!=lag_dir: reason=f"Direction mismatch: model {side}, lag {lag_dir}"
    elif px>=.92 and lag<lag_strong_score: reason=f"Market already priced ({side} ask {px:.3f})"
    elif not checks[4][0]: reason=f"Spread too wide ({spr:.3f})"
    elif not checks[5][0]: reason=f"Liquidity too low (${liq:.0f})"
    elif not checks[6][0]: reason=f"Entry price outside allowed range ({px:.3f})"
    elif gap>max_model_market_gap and lag<lag_strong_score:
        reason=f"Model/market gap {gap:.1%} needs stronger lag confirmation"
    elif not checks[7][0]: reason=f"Model confidence too low ({raw_side:.1%})"
    elif not checks[8][0]: reason=f"Edge too small ({edge:.1%})"
    elif not checks[9][0]: reason=f"Lag score too weak ({lag:.2f})"
    else:
        if px<=extreme_price_threshold and (abs(move_bps)<extreme_min_move_bps or seconds_left<extreme_min_seconds_left):
            reason="Extreme-price setup lacks confirmation"
        else:
            return Signal("BUY",side,prob,px,edge,"Lag + microstructure filters passed",
                          raw_up,blend_up,mkt_up,gap,lag,lag_dir,passed,len(checks))
    return Signal("SKIP",side,prob,px,edge,reason,raw_up,blend_up,mkt_up,gap,lag,lag_dir,passed,len(checks))
