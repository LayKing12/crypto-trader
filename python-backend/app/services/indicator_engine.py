"""
Indicator Engine — deterministic technical indicators.
All functions are pure (no I/O). Results rounded to 2 dp.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from app.utils.math_utils import (
    calc_rsi, calc_ema, calc_atr, calc_volatility_30d, calc_volume_ratio,
    calc_macd, calc_obv_trend,
)
from app.utils.logging_utils import get_logger

log = get_logger(__name__)


@dataclass
class Indicators:
    symbol: str
    price: float
    rsi: float | None
    ema20: float | None
    ema50: float | None
    ema200: float | None
    ema21: float | None
    ema55: float | None
    ema100: float | None
    atr: float | None
    volatility_30d: float | None
    volume_ratio: float
    regime: str  # bull_trend / bear_trend / ranging
    macd_cross_up: bool = field(default=False)   # MACD line crossed above signal
    obv_rising: bool = field(default=False)       # OBV trending up


def compute_indicators(
    symbol: str,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> Indicators:
    """
    Requires at least 201 candles for EMA200 accuracy.
    Shorter history degrades gracefully (None values).
    """
    if not closes:
        raise ValueError(f"No candle data for {symbol}")

    price = closes[-1]
    rsi = calc_rsi(closes)
    ema20 = calc_ema(closes, 20)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    ema55 = calc_ema(closes, 55)
    ema100 = calc_ema(closes, 100)
    ema200 = calc_ema(closes, 200)
    atr = calc_atr(highs, lows, closes)
    vol_30d = calc_volatility_30d(closes)
    vol_ratio = calc_volume_ratio(volumes[-1] if volumes else 0.0, volumes)
    regime = _detect_regime(ema20, ema50, ema200)

    macd_line, macd_signal, _ = calc_macd(closes)
    macd_cross_up = bool(macd_line and macd_signal and macd_line > macd_signal)
    obv_rising = calc_obv_trend(closes, volumes)

    log.debug("indicators_computed", symbol=symbol, rsi=rsi, ema21=ema21, ema55=ema55, macd_cross_up=macd_cross_up)

    return Indicators(
        symbol=symbol,
        price=price,
        rsi=rsi,
        ema20=ema20,
        ema21=ema21,
        ema50=ema50,
        ema55=ema55,
        ema100=ema100,
        ema200=ema200,
        atr=atr,
        volatility_30d=vol_30d,
        volume_ratio=vol_ratio,
        regime=regime,
        macd_cross_up=macd_cross_up,
        obv_rising=obv_rising,
    )


def _detect_regime(ema20: float | None, ema50: float | None, ema200: float | None) -> str:
    if ema20 and ema50 and ema200:
        if ema20 > ema50 > ema200:
            return "bull_trend"
        if ema20 < ema50 < ema200:
            return "bear_trend"
    return "ranging"
