"""Signaux eToro (actions + or) à partir d'indicator_engine / scoring_engine.

Pont entre le pipeline d'analyse existant (Kraken) et l'Agent Portfolio eToro :
  bougies eToro (15 min)  ->  indicator_engine.compute_indicators
                          ->  scoring_engine.compute_scores  (market_score 0-100)
                          ->  etoro.models.Signal (BUY uniquement en v1)

Le seuil d'exécution (market_score >= ETORO_MIN_SCORE, 70 par défaut) est appliqué
ensuite par etoro.risk_guard, exactement comme risk_engine côté Kraken.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import logging

from cryptomind import indicator_engine, scoring_engine

from .models import Side, Signal

log = logging.getLogger(__name__)

CANDLE_INTERVAL = "FifteenMinutes"
CANDLE_COUNT = 250          # >= 201 bougies pour une EMA200 fiable
MIN_CANDLES = 30            # même minimum que _run_analysis côté Kraken
MAX_CONCURRENCY = 3         # quota eToro market-data : 120 req / 60 s, partagé


async def build_signal(agent, symbol: str) -> Signal | None:
    """Calcule le market_score d'un instrument eToro via le pipeline CryptoMind."""
    inst = agent.instruments_by_symbol.get(symbol.upper())
    if inst is None:
        log.warning("signal : symbole inconnu %s", symbol)
        return None
    candles = await agent.service.get_candles(inst.instrument_id, CANDLE_INTERVAL, CANDLE_COUNT)
    if len(candles) < MIN_CANDLES:
        log.warning("signal %s : %d bougies, minimum %d", symbol, len(candles), MIN_CANDLES)
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]

    ind = indicator_engine.compute_indicators(symbol.upper(), closes, highs, lows, volumes)
    scores = scoring_engine.compute_scores(ind)   # pas de whale/OI/funding pour actions & or
    log.info("signal %s : market_score=%.1f regime=%s rsi=%s", symbol.upper(), scores.market_score, ind.regime, round(ind.rsi, 1) if ind.rsi is not None else None)
    return Signal(
        instrument_id=inst.instrument_id,
        symbol=inst.symbol,
        side=Side.BUY,
        market_score=max(0.0, min(100.0, float(scores.market_score))),
        generated_at=datetime.now(timezone.utc),
    )


def make_signal_provider(agent):
    """Retourne le callable async attendu par PortfolioAgent.run_forever()."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _one(symbol: str) -> Signal | None:
        async with sem:
            try:
                return await build_signal(agent, symbol)
            except Exception as e:  # noqa: BLE001 — un symbole en échec ne bloque pas les autres
                log.error("signal %s : erreur %s", symbol, e)
                return None

    async def provider() -> list[Signal]:
        if not agent.instruments_by_symbol:
            await agent.load_universe()
        results = await asyncio.gather(*(_one(s) for s in agent.settings.universe))
        return [s for s in results if s is not None]

    return provider
