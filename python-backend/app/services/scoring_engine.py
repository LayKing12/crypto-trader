"""
Scoring Engine — converts indicators into 0–100 scores.
Weights match the V1 specification exactly.
"""
from __future__ import annotations
from dataclasses import dataclass
from app.services.indicator_engine import Indicators
from app.utils.math_utils import clamp
from app.utils.logging_utils import get_logger
from app.config import get_settings

log = get_logger(__name__)
settings = get_settings()


@dataclass
class Scores:
    trend_score: float
    rsi_score: float
    volume_score: float
    whale_score: float
    sentiment_score: float
    oi_score: float
    funding_score: float
    fear_greed_score: float
    market_score: float   # weighted composite 0–100
    confluence_count: int


def score_trend(ind: Indicators) -> float:
    e20, e50, e200 = ind.ema20, ind.ema50, ind.ema200
    if e20 and e50 and e200:
        if e20 > e50 > e200:
            return 100.0
        if e20 > e50:
            return 70.0
        if e20 < e200:
            return 30.0
    return 50.0


def score_rsi(rsi: float | None) -> float:
    if rsi is None:
        return 50.0
    if rsi < 30:
        return 90.0
    if 30 <= rsi < 40:
        return 70.0
    if 40 <= rsi <= 60:
        return 50.0
    if rsi > 70:
        return 20.0
    return 40.0


def score_volume(volume_ratio: float) -> float:
    if volume_ratio > 2.0:
        return 85.0
    if volume_ratio > 1.5:
        return 70.0
    return 50.0


def score_oi(oi_delta_pct: float | None) -> float:
    """Rising open interest = bullish interest."""
    if oi_delta_pct is None:
        return 50.0
    if oi_delta_pct > 5:
        return 80.0
    if oi_delta_pct > 0:
        return 65.0
    if oi_delta_pct < -5:
        return 25.0
    return 50.0


def score_funding(funding_rate: float | None) -> float:
    """Negative funding → longs being paid → bullish signal."""
    if funding_rate is None:
        return 50.0
    if funding_rate < -0.01:
        return 80.0
    if funding_rate < 0:
        return 65.0
    if funding_rate > 0.05:
        return 20.0
    return 50.0


def score_fear_greed(index: float | None) -> float:
    """
    Contrarian: extreme fear = buy opportunity.
    0=Extreme Fear … 100=Extreme Greed
    """
    if index is None:
        return 50.0
    if index < 20:
        return 85.0  # extreme fear → opportunity
    if index < 40:
        return 65.0
    if index > 75:
        return 25.0  # extreme greed → caution
    return 50.0


def compute_scores(
    ind: Indicators,
    whale_score: float = 50.0,
    sentiment_score: float = 50.0,
    oi_delta_pct: float | None = None,
    funding_rate: float | None = None,
    fear_greed_index: float | None = None,
) -> Scores:
    t = score_trend(ind)
    r = score_rsi(ind.rsi)
    v = score_volume(ind.volume_ratio)
    w = clamp(whale_score)
    s = clamp(sentiment_score)
    oi = score_oi(oi_delta_pct)
    f = score_funding(funding_rate)
    fg = score_fear_greed(fear_greed_index)

    # Weighted composite (weights sum = 1.0)
    market_score = (
        t * 0.25 +
        r * 0.15 +
        v * 0.10 +
        w * 0.15 +
        s * 0.10 +
        oi * 0.10 +
        f * 0.05 +
        fg * 0.10
    )

    # Confluence: count signals clearly aligned bullish (score ≥ 65)
    confluence_count = sum(1 for score in [t, r, v, w, s, oi, f, fg] if score >= 65)

    log.debug(
        "scores_computed",
        symbol=ind.symbol,
        market_score=round(market_score, 2),
        confluence=confluence_count,
    )

    return Scores(
        trend_score=t,
        rsi_score=r,
        volume_score=v,
        whale_score=w,
        sentiment_score=s,
        oi_score=oi,
        funding_score=f,
        fear_greed_score=fg,
        market_score=clamp(market_score),
        confluence_count=confluence_count,
    )
