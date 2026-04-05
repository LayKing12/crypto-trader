"""Pure math helpers — no I/O, no side effects."""
from __future__ import annotations
import numpy as np


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, round(value, 2)))


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return round((new - old) / old * 100, 4)


# ── Technical indicators ────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI — same algo as the frontend technical.js implementation."""
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    arr = np.array(closes, dtype=float)
    k = 2 / (period + 1)
    ema = arr[:period].mean()
    for price in arr[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_prev_close = abs(highs[i] - closes[i - 1])
        low_prev_close = abs(lows[i] - closes[i - 1])
        trs.append(max(high_low, high_prev_close, low_prev_close))
    # Wilder smoothing
    atr = np.mean(trs[:period])
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def calc_volatility_30d(closes: list[float]) -> float | None:
    """Standard deviation of log returns over last 30 periods."""
    if len(closes) < 31:
        return None
    prices = np.array(closes[-31:], dtype=float)
    log_returns = np.log(prices[1:] / prices[:-1])
    return round(float(log_returns.std()), 6)


def calc_volume_ratio(current_volume: float, volumes: list[float], period: int = 20) -> float:
    if len(volumes) < period:
        return 1.0
    avg = np.mean(volumes[-period:])
    if avg == 0:
        return 1.0
    return round(current_volume / avg, 3)
