"""
Claude Service — désactivé (pas de crédits).
Retourne neutral par défaut.
"""
from __future__ import annotations
from app.utils.logging_utils import get_logger

log = get_logger(__name__)


async def analyze(
    symbol: str,
    regime: str,
    market_score: float,
    confidence_score: float,
    risk_score: float,
    rsi: float | None,
    trend_score: float,
    volume_ratio: float,
    whale_activity: str = "unknown",
    sentiment: str = "neutral",
    volatility_regime: str = "moderate",
) -> dict:
    log.info("claude_disabled", symbol=symbol)
    return _neutral_default()


def should_execute(
    technical_bias: str,
    claude_result: dict,
) -> tuple[bool, str]:
    """Claude désactivé — laisse toujours passer."""
    return True, "claude_disabled"


def _neutral_default() -> dict:
    return {
        "narrative_bias": "neutral",
        "confidence_adjustment": 0,
        "risk_warning": "claude_disabled",
        "invalid_scenario": "n/a",
        "macro_comment": "Claude désactivé — trading sur signaux techniques uniquement.",
    }