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


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float | None, float | None, float | None]:
    """Returns (macd_line, signal_line, histogram) for the last candle."""
    if len(closes) < slow + signal:
        return None, None, None
    arr = np.array(closes, dtype=float)
    fast_ema = _ema_array(arr, fast)
    slow_ema = _ema_array(arr, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema_array(macd_line, signal)
    histogram = macd_line - signal_line
    return round(float(macd_line[-1]), 6), round(float(signal_line[-1]), 6), round(float(histogram[-1]), 6)


def _ema_array(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    result = np.empty_like(arr)
    result[:period] = arr[:period].mean()
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


def calc_obv_trend(closes: list[float], volumes: list[float], period: int = 10) -> bool:
    """Returns True if OBV is trending up over the last `period` bars."""
    if len(closes) < period + 1 or len(volumes) < period + 1:
        return False
    obv = 0.0
    obv_series = []
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        obv_series.append(obv)
    if len(obv_series) < period:
        return False
    recent = obv_series[-period:]
    # OBV is rising if linear regression slope is positive
    x = np.arange(len(recent), dtype=float)
    slope = np.polyfit(x, recent, 1)[0]
    return slope > 0
