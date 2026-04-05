"""
Claude Service — strategic AI layer ONLY.
Claude provides qualitative insight. It NEVER determines position size,
NEVER overrides the risk engine, and NEVER generates external data.
"""
from __future__ import annotations
import json
import anthropic
from app.config import get_settings
from app.utils.logging_utils import get_logger

log = get_logger(__name__)
settings = get_settings()

_client: anthropic.AsyncAnthropic | None = None

SYSTEM_PROMPT = """You are a senior quantitative crypto strategist.

Rules (non-negotiable):
- Use ONLY data provided in the user message.
- NEVER hallucinate prices, indicators, or external events.
- NEVER invent external data or news.
- NEVER suggest a position size — that is handled by the risk engine.
- Respond ONLY in valid JSON, no markdown, no prose outside the JSON.
- If the scenario is invalid or data is insufficient, set narrative_bias to "neutral".

Your role: provide qualitative strategic context to complement deterministic signals."""

RESPONSE_SCHEMA = {
    "narrative_bias": "long | short | neutral",
    "confidence_adjustment": "integer between -10 and +10",
    "risk_warning": "string or null",
    "invalid_scenario": "condition that would invalidate this setup",
    "macro_comment": "one concise sentence"
}


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


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
    # ── Claude désactivé (pas de crédits) — technical signals only ──
    log.info("claude_disabled", symbol=symbol)
    return _neutral_default()


def should_execute(
    technical_bias: str,
    claude_result: dict,
) -> tuple[bool, str]:
    """
    Decision gate:
    - If Claude says opposite bias to technical signal → skip
    - Claude can only cancel, never force a trade
    """
    claude_bias = claude_result.get("narrative_bias", "neutral")
    if claude_bias == "neutral":
        return True, "claude_neutral_no_block"
    if technical_bias == "long" and claude_bias == "short":
        return False, "claude_vetoed_long_signal"
    if technical_bias == "short" and claude_bias == "long":
        return False, "claude_vetoed_short_signal"
    return True, "claude_confirmed"


def _neutral_default() -> dict:
    return {
        "narrative_bias": "neutral",
        "confidence_adjustment": 0,
        "risk_warning": "claude_disabled",
        "invalid_scenario": "n/a",
        "macro_comment": "Claude désactivé — trading sur signaux techniques uniquement.",
    }