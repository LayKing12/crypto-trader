"""Tests — watch/market_watch_service.py (aucune action, seulement des observations)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etoro.models import Instrument, Quote
from watch.config import WatchSettings
from watch.market_watch_service import MarketWatch
from watch.observation_log import ObservationLog


def _settings(**kw) -> WatchSettings:
    base = dict(WATCH_ENABLED="true", WATCH_KRAKEN_PAIRS="BTCUSD", WATCH_ETORO_SYMBOLS="AAPL",
                WATCH_MOVE_THRESHOLD_PCT="2.0", WATCH_LOG_PATH="")
    base.update(kw)
    return WatchSettings(**base)


class FakeEtoro:
    def __init__(self, prices):
        self.prices = prices  # prix ask successifs

    async def get_instruments(self, symbols):
        return [Instrument(symbol=s, instrument_id=1001 + i) for i, s in enumerate(symbols)]

    async def get_quotes(self, ids):
        p = self.prices.pop(0)
        return [Quote(instrument_id=i, bid=p - 0.01, ask=p, timestamp=datetime.now(timezone.utc)) for i in ids]


def _kraken(prices):
    async def fn(pair):
        return prices.pop(0)
    return fn


@pytest.mark.asyncio
async def test_first_tick_sets_reference_without_observation():
    log = ObservationLog(None)
    mw = MarketWatch(_settings(WATCH_ETORO_SYMBOLS=""), log, kraken_price_fn=_kraken([100.0]))
    obs = await mw.tick()
    assert obs == [] and mw.reference("kraken", "BTCUSD") == 100.0


@pytest.mark.asyncio
async def test_move_below_threshold_is_silent_above_is_recorded():
    log = ObservationLog(None)
    mw = MarketWatch(_settings(WATCH_ETORO_SYMBOLS=""), log, kraken_price_fn=_kraken([100.0, 101.0, 104.0]))
    await mw.tick()
    assert await mw.tick() == []                      # +1 % < 2 %
    obs = await mw.tick()                             # +4 % vs référence 100
    assert len(obs) == 1 and obs[0].kind == "price_move" and obs[0].symbol == "BTCUSD"
    assert obs[0].detail["change_pct"] == pytest.approx(4.0)
    assert mw.reference("kraken", "BTCUSD") == 104.0  # référence remise à jour
    assert len(log.recent(kind="price_move")) == 1


@pytest.mark.asyncio
async def test_etoro_source_via_fake_service():
    log = ObservationLog(None)
    mw = MarketWatch(_settings(WATCH_KRAKEN_PAIRS=""), log, etoro_service=FakeEtoro([200.0, 190.0]))
    await mw.tick()
    obs = await mw.tick()
    assert len(obs) == 1 and obs[0].source == "etoro" and obs[0].symbol == "AAPL"
    assert obs[0].detail["change_pct"] == pytest.approx(-5.0, abs=0.01)


@pytest.mark.asyncio
async def test_failing_source_does_not_block_the_other():
    log = ObservationLog(None)

    async def broken(pair):
        raise RuntimeError("kraken down")

    mw = MarketWatch(_settings(), log, kraken_price_fn=broken, etoro_service=FakeEtoro([50.0, 60.0]))
    await mw.tick()
    obs = await mw.tick()
    assert any(o.source == "etoro" and o.kind == "price_move" for o in obs)
    assert mw.last_error is not None
