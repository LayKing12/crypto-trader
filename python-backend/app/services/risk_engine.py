"""
Risk Engine — the capital protection shield.
This module is the single source of truth for position sizing and risk rules.
Claude CANNOT override these decisions.
"""
from __future__ import annotations
from dataclasses import dataclass
from app.utils.math_utils import clamp
from app.utils.logging_utils import get_logger
from app.config import get_settings

log = get_logger(__name__)
settings = get_settings()


@dataclass
class RiskState:
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    current_drawdown_pct: float = 0.0
    trading_enabled: bool = True
    current_base_size_pct: float = 2.0


@dataclass
class RiskAssessment:
    risk_score: float           # 0–100 (higher = more risk)
    position_size_pct: float    # 1–5%
    stop_loss_pct: float        # always -7%
    confidence_score: float     # 0–100
    trading_enabled: bool
    reason: str


def compute_risk_score(
    volatility_30d: float | None,
    state: RiskState,
    vol_threshold: float | None = None,
) -> float:
    """
    risk_score = volatility_weight + drawdown_weight + losing_streak_weight
    Clamped 0–100.
    """
    threshold = vol_threshold or settings.volatility_high_threshold
    score = 0.0

    if volatility_30d and volatility_30d > threshold:
        score += 20.0

    if state.consecutive_losses >= settings.max_consecutive_losses:
        score += 15.0

    if state.current_drawdown_pct > 8.0:
        score += 20.0

    return clamp(score)


def compute_confidence_score(
    market_score: float,
    confluence_count: int,
    volatility_30d: float | None,
    vol_threshold: float | None = None,
) -> float:
    """
    confidence = (market_score * 0.6) + (confluence_count * 5) - volatility_penalty
    """
    threshold = vol_threshold or settings.volatility_high_threshold
    penalty = 15.0 if (volatility_30d and volatility_30d > threshold) else 0.0
    raw = (market_score * 0.6) + (confluence_count * 5) - penalty
    return clamp(raw)


def compute_position_size(
    confidence_score: float,
    risk_score: float,
    state: RiskState,
) -> float:
    """
    size = base_size * (confidence / 100) * (1 - risk / 100)
    Hard limits: min 1%, max 5%. Never exceeds MAX_POSITION_SIZE_PCT.
    """
    base = state.current_base_size_pct
    size = base * (confidence_score / 100) * (1 - risk_score / 100)
    return clamp(size, 1.0, settings.max_position_size_pct)


def apply_adaptive_rules(state: RiskState) -> RiskState:
    """
    Mutates (or returns updated) state based on adaptive rules:
    - 3 consecutive losses → base size = 1%
    - 5 consecutive wins → cap raised to 4%
    - drawdown > 12% → trading disabled
    """
    updated = RiskState(
        consecutive_losses=state.consecutive_losses,
        consecutive_wins=state.consecutive_wins,
        current_drawdown_pct=state.current_drawdown_pct,
        trading_enabled=state.trading_enabled,
        current_base_size_pct=state.current_base_size_pct,
    )

    if state.current_drawdown_pct > settings.drawdown_disable_pct:
        updated.trading_enabled = False
        log.warning("trading_disabled_drawdown", drawdown=state.current_drawdown_pct)

    if state.consecutive_losses >= settings.max_consecutive_losses:
        updated.current_base_size_pct = 1.0
        log.warning("base_size_reduced", consecutive_losses=state.consecutive_losses)

    if state.consecutive_wins >= 5:
        updated.current_base_size_pct = min(4.0, settings.max_position_size_pct)
        log.info("base_size_increased", consecutive_wins=state.consecutive_wins)

    return updated


def assess(
    market_score: float,
    confluence_count: int,
    volatility_30d: float | None,
    state: RiskState,
) -> RiskAssessment:
    """Full risk assessment pipeline."""
    state = apply_adaptive_rules(state)

    if not state.trading_enabled:
        return RiskAssessment(
            risk_score=100.0,
            position_size_pct=0.0,
            stop_loss_pct=settings.stop_loss_pct,
            confidence_score=0.0,
            trading_enabled=False,
            reason=f"Trading disabled — drawdown {state.current_drawdown_pct:.1f}% exceeds {settings.drawdown_disable_pct}%",
        )

    risk_score = compute_risk_score(volatility_30d, state)
    confidence_score = compute_confidence_score(market_score, confluence_count, volatility_30d)
    position_size_pct = compute_position_size(confidence_score, risk_score, state)

    log.info(
        "risk_assessment",
        risk_score=risk_score,
        confidence_score=confidence_score,
        position_size_pct=position_size_pct,
        trading_enabled=True,
    )

    return RiskAssessment(
        risk_score=risk_score,
        position_size_pct=position_size_pct,
        stop_loss_pct=settings.stop_loss_pct,
        confidence_score=confidence_score,
        trading_enabled=True,
        reason="ok",
    )
