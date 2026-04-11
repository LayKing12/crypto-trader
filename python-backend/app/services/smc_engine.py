"""
Kasper SMC Ultra — Smart Money Concepts strategy engine.

Concepts implemented:
  - Order Blocks (OB) with FVG validation
  - Asian Range session filter (23:00–05:00 UTC blocks new entries)
  - Multi-Timeframe structure analysis (1m / 5m / 15m / 1h / 4h)
  - Liquidity sweep detection (false breakout = institutional grab)
  - SL: 0.15% buffer below OB low  |  TP: 1:2 Risk/Reward

Score thresholds:
  smc_score >= 75 AND market_score >= 65 required to open a trade
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── Constants ──────────────────────────────────────────────────────────────

SMC_MIN_SCORE      = 75       # minimum SMC score to allow entry
MARKET_MIN_SCORE   = 65       # companion market_score threshold (replaces 70)
SMC_COOLDOWN       = 3600     # 1-hour cooldown per pair after a SMC signal

# SL buffer below OB low ("3 pips" equivalent for crypto)
OB_SL_BUFFER_PCT   = 0.0015   # 0.15 %
RR_RATIO           = 2.0      # 1:2 risk/reward for TP

# Asian session in UTC (00:00–06:00 UTC+1 = 23:00–05:00 UTC)
ASIAN_SESSION_START_UTC = 23
ASIAN_SESSION_END_UTC   = 5

# ── In-memory 1h cooldown tracker ─────────────────────────────────────────

_smc_last_signal: dict[str, float] = {}


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class OrderBlock:
    type: str          # "bullish" | "bearish"
    high: float
    low: float
    mid: float
    fvg_confirmed: bool
    candle_idx: int
    mitigated: bool = False


@dataclass
class FVGZone:
    high: float
    low: float
    type: str          # "bullish" | "bearish"
    candle_idx: int


@dataclass
class SMCAnalysis:
    smc_score: float              # 0–100
    mtf_score: int                # 0–5 (number of bullish TFs)
    active_ob: OrderBlock | None
    fvg_zones: list[FVGZone] = field(default_factory=list)
    liquidity_swept: bool = False
    asian_range_blocked: bool = False
    cooldown_blocked: bool = False
    bias: str = "neutral"         # "bullish" | "bearish" | "neutral"
    entry_price: float | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    reason: str = ""


# ── Asian Range filter ─────────────────────────────────────────────────────

def is_asian_session() -> bool:
    """True during low-liquidity Asian session (23:00–05:00 UTC)."""
    hour = datetime.now(timezone.utc).hour
    return hour >= ASIAN_SESSION_START_UTC or hour < ASIAN_SESSION_END_UTC


# ── FVG detection ──────────────────────────────────────────────────────────

def detect_fvg(candles: list[dict]) -> list[FVGZone]:
    """
    Fair Value Gap: price imbalance between candle[i-1] and candle[i+1].
      Bullish FVG : candle[i-1].high  <  candle[i+1].low
      Bearish FVG : candle[i-1].low   >  candle[i+1].high
    """
    zones: list[FVGZone] = []
    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]
        nxt  = candles[i + 1]

        if prev["high"] < nxt["low"]:
            zones.append(FVGZone(
                high=nxt["low"], low=prev["high"],
                type="bullish", candle_idx=i,
            ))
        elif prev["low"] > nxt["high"]:
            zones.append(FVGZone(
                high=prev["low"], low=nxt["high"],
                type="bearish", candle_idx=i,
            ))

    return zones[-10:]  # keep last 10 zones


# ── Order Block detection ──────────────────────────────────────────────────

def detect_order_blocks(candles: list[dict]) -> list[OrderBlock]:
    """
    Bullish OB: the last bearish candle before a significant bullish impulse.
    Conditions:
      - Candle i is bearish (close < open)
      - Candle i+1 closes above OB high (impulse breaks structure)
      - Impulse >= 50 % of OB body size
    FVG confirmed: candle[i+2].low > candle[i].high  (gap left behind)
    Mitigated: current price has traded back below OB low.
    """
    obs: list[OrderBlock] = []
    n = len(candles)
    last_close = candles[-1]["close"] if candles else 0.0

    for i in range(1, n - 3):
        c = candles[i]
        if c["close"] >= c["open"]:   # must be bearish
            continue

        ob_body = abs(c["open"] - c["close"])
        if ob_body <= 0:
            continue

        next1 = candles[i + 1]
        next2 = candles[i + 2]

        # Impulse must close above OB high
        if next1["close"] <= c["high"]:
            continue

        impulse = next1["close"] - c["close"]
        if impulse < ob_body * 0.5:
            continue

        fvg_confirmed = next2["low"] > c["high"]
        mitigated = last_close < c["low"]

        obs.append(OrderBlock(
            type="bullish",
            high=c["high"],
            low=c["low"],
            mid=round((c["high"] + c["low"]) / 2, 8),
            fvg_confirmed=fvg_confirmed,
            candle_idx=i,
            mitigated=mitigated,
        ))

    return obs[-5:]  # keep last 5 OBs


# ── Market structure analysis ──────────────────────────────────────────────

def _find_swings(candles: list[dict], lookback: int = 2) -> tuple[list, list]:
    """Return (swing_highs, swing_lows) as lists of (idx, price)."""
    highs: list[tuple[int, float]] = []
    lows:  list[tuple[int, float]] = []
    n = len(candles)

    for i in range(lookback, n - lookback):
        h = candles[i]["high"]
        lo = candles[i]["low"]

        if all(h > candles[i - j]["high"] for j in range(1, lookback + 1)) and \
           all(h > candles[i + j]["high"] for j in range(1, lookback + 1)):
            highs.append((i, h))

        if all(lo < candles[i - j]["low"] for j in range(1, lookback + 1)) and \
           all(lo < candles[i + j]["low"] for j in range(1, lookback + 1)):
            lows.append((i, lo))

    return highs, lows


def analyze_structure(candles: list[dict]) -> str:
    """
    Market structure bias over the last 50 candles.
    Bullish  → HH + HL  (higher highs and higher lows)
    Bearish  → LL + LH  (lower lows and lower highs)
    Ranging  → mixed
    """
    if len(candles) < 15:
        return "ranging"

    swing_highs, swing_lows = _find_swings(candles[-50:])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    last_sh, prev_sh = swing_highs[-1][1], swing_highs[-2][1]
    last_sl, prev_sl = swing_lows[-1][1],  swing_lows[-2][1]

    hh = last_sh > prev_sh
    hl = last_sl > prev_sl
    ll = last_sl < prev_sl
    lh = last_sh < prev_sh

    if hh and hl:
        return "bullish"
    if ll and lh:
        return "bearish"
    return "ranging"


# ── Liquidity sweep detection ─────────────────────────────────────────────

def check_liquidity_sweep(candles: list[dict], lookback: int = 20) -> bool:
    """
    Bullish sweep: one of the last 3 candles wicks *below* a prior swing low
    but closes *above* it — institutions grabbed sell-side liquidity before reversing.
    """
    if len(candles) < lookback + 3:
        return False

    window = candles[-lookback:]
    prior_candles = window[:-3]
    last_three    = window[-3:]

    if not prior_candles:
        return False

    prior_swing_low = min(c["low"] for c in prior_candles)

    for c in last_three:
        if c["low"] < prior_swing_low and c["close"] > prior_swing_low:
            return True

    return False


# ── MTF score ─────────────────────────────────────────────────────────────

def compute_mtf_score(candles_by_tf: dict[str, list[dict]]) -> int:
    """
    Count how many of the 5 standard timeframes show bullish structure.
    Returns 0–5.
    """
    tfs = ["1m", "5m", "15m", "1h", "4h"]
    return sum(1 for tf in tfs if analyze_structure(candles_by_tf.get(tf, [])) == "bullish")


# ── Main analysis entry point ──────────────────────────────────────────────

def analyze(symbol: str, candles_by_tf: dict[str, list[dict]]) -> SMCAnalysis:
    """
    Full Kasper SMC Ultra analysis.
    Primary (entry) timeframe: 15m.
    Returns SMCAnalysis with smc_score and computed SL/TP prices.

    Score breakdown (max 100):
      MTF alignment     8 pts × 5 TFs = 40
      Active OB present               = 20
      FVG confirmed on OB             = 15
      Liquidity sweep                 = 15
      15m structure bullish           = 10
    """
    sym = symbol.upper()
    asian_blocked = is_asian_session()

    # 1h cooldown check
    now = time.time()
    cooldown_blocked = (now - _smc_last_signal.get(sym, 0)) < SMC_COOLDOWN

    # Primary candles
    candles_15m = candles_by_tf.get("15m", [])
    if len(candles_15m) < 15:
        return SMCAnalysis(
            smc_score=0, mtf_score=0, active_ob=None,
            asian_range_blocked=asian_blocked,
            cooldown_blocked=cooldown_blocked,
            reason="insufficient_15m_data",
        )

    current_price = candles_15m[-1]["close"]

    # MTF structure
    mtf_score = compute_mtf_score(candles_by_tf)

    # Order Blocks on 15m
    all_obs = detect_order_blocks(candles_15m)
    active_ob: OrderBlock | None = None
    for ob in reversed(all_obs):
        if ob.mitigated:
            continue
        # Price must be inside or just above the OB (within 5 %)
        if ob.low <= current_price <= ob.high * 1.05:
            active_ob = ob
            break

    # FVG zones on 15m
    fvg_zones = detect_fvg(candles_15m)

    # Liquidity sweep on 15m
    liquidity_swept = check_liquidity_sweep(candles_15m)

    # 15m market structure
    structure_15m = analyze_structure(candles_15m)

    # ── Score ─────────────────────────────────────────────────────────
    score: float = 0.0
    score += mtf_score * 8                           # 0 – 40

    if active_ob:
        score += 20
        if active_ob.fvg_confirmed:
            score += 15

    if liquidity_swept:
        score += 15

    if structure_15m == "bullish":
        score += 10

    # ── Bias ──────────────────────────────────────────────────────────
    if mtf_score >= 3 and structure_15m == "bullish":
        bias = "bullish"
    elif mtf_score <= 1 and structure_15m == "bearish":
        bias = "bearish"
    else:
        bias = "neutral"

    # ── Trade levels ──────────────────────────────────────────────────
    entry_price: float | None = None
    sl_price:    float | None = None
    tp_price:    float | None = None

    if active_ob and bias == "bullish":
        entry_price = current_price
        sl_price    = round(active_ob.low * (1 - OB_SL_BUFFER_PCT), 8)
        risk        = entry_price - sl_price
        tp_price    = round(entry_price + risk * RR_RATIO, 8)

    # ── Reason string ─────────────────────────────────────────────────
    ob_tag = "no"
    if active_ob:
        ob_tag = "fvg" if active_ob.fvg_confirmed else "yes"

    reason = (
        f"mtf={mtf_score}/5 | struct={structure_15m} | "
        f"ob={ob_tag} | sweep={'yes' if liquidity_swept else 'no'} | "
        f"score={round(score, 1)}"
    )

    return SMCAnalysis(
        smc_score=round(score, 1),
        mtf_score=mtf_score,
        active_ob=active_ob,
        fvg_zones=fvg_zones,
        liquidity_swept=liquidity_swept,
        asian_range_blocked=asian_blocked,
        cooldown_blocked=cooldown_blocked,
        bias=bias,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        reason=reason,
    )


def record_signal(symbol: str) -> None:
    """Call when a SMC-gated trade opens to start the 1-hour cooldown."""
    _smc_last_signal[symbol.upper()] = time.time()
