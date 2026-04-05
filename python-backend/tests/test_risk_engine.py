"""
Tests unitaires — Risk Engine
Test 1: Jamais plus de 5% du capital
Test 2: Stop-loss toujours à -7%
Test 3: Gestion des pertes consécutives
Test 4: Trading désactivé si drawdown > 12%
"""
import pytest
from app.services.risk_engine import (
    RiskState, assess, compute_position_size,
    compute_risk_score, apply_adaptive_rules,
)
from app.config import get_settings

settings = get_settings()


def make_state(**kwargs) -> RiskState:
    defaults = {
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "current_drawdown_pct": 0.0,
        "trading_enabled": True,
        "current_base_size_pct": 2.0,
    }
    defaults.update(kwargs)
    return RiskState(**defaults)


class TestPositionSizing:
    def test_never_exceeds_5_pct(self):
        """Test 1: Position size must NEVER exceed 5% of capital."""
        state = make_state(current_base_size_pct=5.0, consecutive_wins=10)
        # Even with perfect confidence and zero risk
        size = compute_position_size(
            confidence_score=100.0,
            risk_score=0.0,
            state=state,
        )
        assert size <= settings.max_position_size_pct, (
            f"Position size {size}% exceeded max {settings.max_position_size_pct}%"
        )

    def test_minimum_1_pct(self):
        """Position size must be at least 1%."""
        state = make_state()
        size = compute_position_size(
            confidence_score=10.0,
            risk_score=90.0,
            state=state,
        )
        assert size >= 1.0

    def test_base_size_respected(self):
        """With 100% confidence and 0% risk, position = base_size."""
        state = make_state(current_base_size_pct=2.0)
        size = compute_position_size(100.0, 0.0, state)
        assert size == 2.0

    def test_high_risk_reduces_size(self):
        """High risk score must reduce position size."""
        state = make_state(current_base_size_pct=2.0)
        size_low_risk = compute_position_size(80.0, 10.0, state)
        size_high_risk = compute_position_size(80.0, 80.0, state)
        assert size_high_risk < size_low_risk


class TestStopLoss:
    def test_stop_loss_at_7_pct(self):
        """Test 2: Stop-loss must always be exactly -7%."""
        state = make_state()
        assessment = assess(
            market_score=75.0,
            confluence_count=5,
            volatility_30d=0.02,
            state=state,
        )
        assert assessment.stop_loss_pct == 7.0, (
            f"Stop-loss should be 7%, got {assessment.stop_loss_pct}%"
        )

    def test_stop_loss_price_calculation(self):
        """Verify stop-loss price math: entry * (1 - 7/100)."""
        entry_price = 50_000.0
        stop_loss_price = round(entry_price * (1 - 7 / 100), 4)
        assert stop_loss_price == 46_500.0


class TestAdaptiveRules:
    def test_3_consecutive_losses_reduces_base_size(self):
        """Test 3: After 3 losses, base size drops to 1%."""
        state = make_state(consecutive_losses=3, current_base_size_pct=2.0)
        updated = apply_adaptive_rules(state)
        assert updated.current_base_size_pct == 1.0

    def test_5_consecutive_wins_increases_base_size(self):
        """5 consecutive wins raises base size to 4%."""
        state = make_state(consecutive_wins=5, current_base_size_pct=2.0)
        updated = apply_adaptive_rules(state)
        assert updated.current_base_size_pct == 4.0

    def test_drawdown_over_12_disables_trading(self):
        """Drawdown > 12% must disable trading entirely."""
        state = make_state(current_drawdown_pct=13.0)
        updated = apply_adaptive_rules(state)
        assert updated.trading_enabled is False

    def test_drawdown_under_12_keeps_trading(self):
        state = make_state(current_drawdown_pct=5.0)
        updated = apply_adaptive_rules(state)
        assert updated.trading_enabled is True


class TestRiskScore:
    def test_high_volatility_adds_risk(self):
        state = make_state()
        score_low_vol = compute_risk_score(0.01, state)
        score_high_vol = compute_risk_score(0.10, state)
        assert score_high_vol > score_low_vol

    def test_risk_score_clamped_0_100(self):
        state = make_state(consecutive_losses=10, current_drawdown_pct=20.0)
        score = compute_risk_score(0.99, state)
        assert 0 <= score <= 100


class TestAssessDisabled:
    def test_trading_disabled_returns_zero_size(self):
        """If trading is disabled, position size must be 0."""
        state = make_state(current_drawdown_pct=15.0, trading_enabled=False)
        assessment = assess(75.0, 5, 0.02, state)
        assert assessment.position_size_pct == 0.0
        assert assessment.trading_enabled is False
