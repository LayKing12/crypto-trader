"""
Strategy Engine — per-pair entry rules, SL/TP config, and global discipline.

In-memory state (resets on restart):
  - consecutive loss counter + 24h circuit breaker
  - per-pair 4h cooldown tracker
DB is consulted live for open trade counts.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.trade import Trade
from app.utils.logging_utils import get_logger

log = get_logger(__name__)

# ── Global guards ──────────────────────────────────────────────────────────

MAX_OPEN_TRADES      = 3      # total simultaneous positions
MAX_PER_PAIR         = 1      # max 1 open trade per symbol
PAIR_COOLDOWN_HOURS  = 4      # min hours between trades on same pair
MAX_CONSEC_LOSSES    = 3      # triggers 24h circuit breaker
CIRCUIT_BREAKER_HOURS = 24
MIN_MARKET_SCORE     = 70     # raised from 50
MIN_RR_RATIO         = 2.0

# ── Per-pair config ────────────────────────────────────────────────────────
# sl_pct / tp_pct: actual SL and TP percentages for trade levels
# size_factor: multiplier applied to the risk engine's position size

PAIR_CONFIG: dict[str, dict] = {
    "BTCUSD":  {"sl_pct": 1.2, "tp_pct": 2.8, "size_factor": 1.0},
    "ETHUSD":  {"sl_pct": 1.4, "tp_pct": 3.2, "size_factor": 1.0},
    "SOLUSD":  {"sl_pct": 2.0, "tp_pct": 4.5, "size_factor": 0.7},
    "ADAUSD":  {"sl_pct": 1.6, "tp_pct": 3.4, "size_factor": 1.0},
    "DOTUSD":  {"sl_pct": 1.8, "tp_pct": 3.8, "size_factor": 1.0},
    "_default": {"sl_pct": 2.0, "tp_pct": 4.0, "size_factor": 1.0},
}

# ── In-memory discipline state ─────────────────────────────────────────────

_consecutive_losses: int = 0
_circuit_breaker_until: datetime | None = None
_pair_last_opened: dict[str, datetime] = {}


# ── Config helpers ─────────────────────────────────────────────────────────

def get_pair_config(symbol: str) -> dict:
    return PAIR_CONFIG.get(symbol.upper(), PAIR_CONFIG["_default"])


def apply_size_factor(symbol: str, base_size_pct: float) -> float:
    return round(base_size_pct * get_pair_config(symbol)["size_factor"], 4)


# ── Discipline callbacks ───────────────────────────────────────────────────

def on_trade_opened(symbol: str) -> None:
    """Record that a trade was opened on this pair (resets cooldown clock)."""
    _pair_last_opened[symbol.upper()] = datetime.now(timezone.utc)
    log.info("pair_cooldown_started", symbol=symbol, hours=PAIR_COOLDOWN_HOURS)


def on_trade_result(won: bool) -> None:
    """Update consecutive loss counter. Call when any trade closes."""
    global _consecutive_losses, _circuit_breaker_until
    if won:
        _consecutive_losses = 0
        log.info("consecutive_losses_reset")
    else:
        _consecutive_losses += 1
        log.warning("consecutive_loss", count=_consecutive_losses)
        if _consecutive_losses >= MAX_CONSEC_LOSSES:
            _circuit_breaker_until = datetime.now(timezone.utc) + timedelta(hours=CIRCUIT_BREAKER_HOURS)
            log.warning(
                "circuit_breaker_triggered",
                until=_circuit_breaker_until.isoformat(),
                losses=_consecutive_losses,
            )


# ── Pre-open guard ─────────────────────────────────────────────────────────

async def can_open_trade(
    db: AsyncSession,
    symbol: str,
    market_score: float,
) -> tuple[bool, str]:
    """
    Returns (True, "") if allowed, else (False, reason_string).
    Checks in order: circuit breaker → pair cooldown → score → per-pair cap → global cap.
    """
    sym = symbol.upper()

    # 1. Global 24h circuit breaker
    if _circuit_breaker_until and datetime.now(timezone.utc) < _circuit_breaker_until:
        remaining_h = int((_circuit_breaker_until - datetime.now(timezone.utc)).total_seconds() / 3600)
        return False, f"circuit_breaker_{remaining_h}h_remaining"

    # 2. Per-pair cooldown
    if sym in _pair_last_opened:
        cooldown_end = _pair_last_opened[sym] + timedelta(hours=PAIR_COOLDOWN_HOURS)
        if datetime.now(timezone.utc) < cooldown_end:
            remaining_m = int((cooldown_end - datetime.now(timezone.utc)).total_seconds() / 60)
            return False, f"pair_cooldown_{sym}_{remaining_m}min"

    # 3. Market score threshold
    if market_score < MIN_MARKET_SCORE:
        return False, f"score_{market_score:.0f}_below_{MIN_MARKET_SCORE}"

    # 4. Max 1 open trade per pair (DB)
    pair_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.symbol == sym, Trade.result == "open")
    )
    if int(pair_result.scalar() or 0) >= MAX_PER_PAIR:
        return False, f"already_open_{sym}"

    # 5. Global max open trades (DB)
    total_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.result == "open")
    )
    if int(total_result.scalar() or 0) >= MAX_OPEN_TRADES:
        return False, f"max_{MAX_OPEN_TRADES}_trades_reached"

    return True, ""


# ── Per-pair technical entry validators ───────────────────────────────────

def _validate_btc(ind) -> tuple[bool, str]:
    """EMA21 > EMA55 (uptrend) + RSI in 45–65 (momentum, not overbought)."""
    if ind.ema21 is None or ind.ema55 is None:
        return False, "btc_ema_not_ready"
    if ind.ema21 <= ind.ema55:
        return False, f"btc_no_uptrend_ema21({ind.ema21:.0f})<=ema55({ind.ema55:.0f})"
    if ind.rsi is None or not (45 <= ind.rsi <= 65):
        return False, f"btc_rsi_not_in_45-65(rsi={ind.rsi})"
    return True, "btc_ok"


def _validate_eth(ind) -> tuple[bool, str]:
    """MACD bullish cross + OBV rising."""
    if not ind.macd_cross_up:
        return False, "eth_no_macd_cross_up"
    if not ind.obv_rising:
        return False, "eth_obv_not_rising"
    return True, "eth_ok"


def _validate_sol(ind) -> tuple[bool, str]:
    """RSI < 55 (not overbought, possible oversold bounce) + not in extreme uptrend."""
    if ind.rsi is None or ind.rsi >= 55:
        return False, f"sol_rsi_too_high(rsi={ind.rsi})"
    return True, "sol_ok"


def _validate_ada(ind) -> tuple[bool, str]:
    """RSI < 38 (oversold) + price within 3% of EMA100 (dynamic support)."""
    if ind.rsi is None or ind.rsi >= 38:
        return False, f"ada_rsi_not_oversold(rsi={ind.rsi})"
    if ind.ema100 is None:
        return False, "ada_ema100_not_ready"
    dist_pct = abs(ind.price - ind.ema100) / ind.ema100 * 100
    if dist_pct > 3.0:
        return False, f"ada_price_far_from_ema100({dist_pct:.1f}%)"
    return True, "ada_ok"


def _validate_dot(ind) -> tuple[bool, str]:
    """HH/HL structure proxy (EMA21 > EMA55) + RSI < 50 (on pullback)."""
    if ind.ema21 is None or ind.ema55 is None:
        return False, "dot_ema_not_ready"
    if ind.ema21 <= ind.ema55:
        return False, "dot_no_hh_hl_structure"
    if ind.rsi is None or ind.rsi >= 50:
        return False, f"dot_not_on_pullback(rsi={ind.rsi})"
    return True, "dot_ok"


_VALIDATORS = {
    "BTCUSD": _validate_btc,
    "ETHUSD": _validate_eth,
    "SOLUSD": _validate_sol,
    "ADAUSD": _validate_ada,
    "DOTUSD": _validate_dot,
}


def validate_pair_entry(symbol: str, ind) -> tuple[bool, str]:
    """
    Run per-pair technical entry check.
    Returns (True, "ok") or (False, reason).
    Unrecognised pairs use a generic RSI-not-overbought check.
    """
    validator = _VALIDATORS.get(symbol.upper())
    if validator:
        return validator(ind)
    # Generic: just require RSI < 70 and not bear regime
    if ind.rsi and ind.rsi > 70:
        return False, f"generic_overbought(rsi={ind.rsi})"
    if ind.regime == "bear_trend":
        return False, "generic_bear_regime"
    return True, "generic_ok"
