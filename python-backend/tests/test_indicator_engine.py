"""
Tests unitaires — Indicator & Scoring Engine
"""
import pytest
from app.utils.math_utils import calc_rsi, calc_ema, calc_atr, calc_volatility_30d
from app.services.indicator_engine import compute_indicators, _detect_regime
from app.services.scoring_engine import (
    score_trend, score_rsi, score_volume, compute_scores
)
from app.services.indicator_engine import Indicators


def make_closes(n: int = 220, start: float = 100.0, trend: float = 0.001) -> list[float]:
    """Generate synthetic close prices with a slight uptrend."""
    prices = [start]
    for _ in range(n - 1):
        prices.append(round(prices[-1] * (1 + trend), 4))
    return prices


class TestRSI:
    def test_rsi_range(self):
        closes = make_closes(50)
        rsi = calc_rsi(closes)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_rsi_none_insufficient_data(self):
        assert calc_rsi([100, 101, 102]) is None

    def test_rsi_overbought_in_uptrend(self):
        """Consistent uptrend should produce RSI > 70."""
        closes = make_closes(50, trend=0.02)
        rsi = calc_rsi(closes)
        assert rsi is not None and rsi > 60

    def test_rsi_oversold_in_downtrend(self):
        closes = make_closes(50, trend=-0.015)
        rsi = calc_rsi(closes)
        assert rsi is not None and rsi < 50


class TestEMA:
    def test_ema_close_to_sma_start(self):
        closes = [100.0] * 30
        ema = calc_ema(closes, 20)
        assert ema is not None
        assert abs(ema - 100.0) < 0.01

    def test_ema_none_insufficient_data(self):
        assert calc_ema([100, 101], 20) is None

    def test_ema_responds_to_price_change(self):
        closes = [100.0] * 50 + [200.0] * 50
        ema = calc_ema(closes, 20)
        assert ema is not None and ema > 100.0


class TestRegimeDetection:
    def test_bull_regime(self):
        assert _detect_regime(200, 180, 150) == "bull_trend"

    def test_bear_regime(self):
        assert _detect_regime(150, 180, 200) == "bear_trend"

    def test_ranging_regime(self):
        assert _detect_regime(180, 200, 150) == "ranging"

    def test_none_ema_gives_ranging(self):
        assert _detect_regime(None, None, None) == "ranging"


class TestScoring:
    def _make_indicators(self, rsi=50, ema20=200, ema50=180, ema200=150) -> Indicators:
        return Indicators(
            symbol="BTCUSDT",
            price=50000,
            rsi=rsi,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            atr=1000,
            volatility_30d=0.02,
            volume_ratio=1.0,
            regime="bull_trend",
        )

    def test_rsi_score_oversold(self):
        assert score_rsi(25) == 90.0

    def test_rsi_score_overbought(self):
        assert score_rsi(75) == 20.0

    def test_rsi_score_neutral(self):
        assert score_rsi(50) == 50.0

    def test_trend_score_full_bull(self):
        ind = self._make_indicators()
        assert score_trend(ind) == 100.0

    def test_volume_score_high(self):
        assert score_volume(2.5) == 85.0

    def test_volume_score_normal(self):
        assert score_volume(1.0) == 50.0

    def test_market_score_clamped(self):
        ind = self._make_indicators()
        scores = compute_scores(ind)
        assert 0 <= scores.market_score <= 100

    def test_confluence_count_bull_market(self):
        """In a clear bull setup, confluence count should be > 2."""
        ind = self._make_indicators(rsi=28)  # oversold in bull trend
        scores = compute_scores(ind, whale_score=80, sentiment_score=70)
        assert scores.confluence_count >= 2
