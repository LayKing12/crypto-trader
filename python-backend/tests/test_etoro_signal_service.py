"""Tests — pont indicator_engine -> signaux eToro (app/services/etoro_signal_service.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from etoro import signal_service as svc
from etoro.config import Settings
from etoro.models import Candle, Instrument, Side


def _candles(n: int, instrument_id: int = 1, trend: float = 0.001) -> list[Candle]:
    out, price, t0 = [], 100.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        nxt = round(price * (1 + trend), 4)
        out.append(Candle(instrument_id=instrument_id, from_date=t0 + timedelta(minutes=15 * i),
                          open=price, high=max(price, nxt) * 1.001, low=min(price, nxt) * 0.999,
                          close=nxt, volume=1000 + i))
        price = nxt
    return out


class FakeService:
    def __init__(self, candles_by_id: dict[int, list[Candle]]):
        self.candles_by_id = candles_by_id
        self.calls: list[tuple[int, str, int]] = []

    async def get_candles(self, instrument_id, interval="FifteenMinutes", count=250):
        self.calls.append((instrument_id, interval, count))
        if instrument_id not in self.candles_by_id:
            raise RuntimeError("boom")
        return self.candles_by_id[instrument_id]

    async def get_instruments(self, symbols):
        return [Instrument(symbol=s, instrument_id=i + 1) for i, s in enumerate(symbols)]


class FakeAgent:
    def __init__(self, settings: Settings, service: FakeService, instruments: dict[str, Instrument]):
        self.settings = settings
        self.service = service
        self.instruments_by_symbol = instruments

    async def load_universe(self):
        for i, s in enumerate(self.settings.universe):
            self.instruments_by_symbol[s] = Instrument(symbol=s, instrument_id=i + 1)
        return self.instruments_by_symbol


def _settings(universe="AAPL,XAUUSD") -> Settings:
    return Settings(ETORO_API_KEY="test", ETORO_UNIVERSE=universe, ETORO_USE_RANKINGS="false", ETORO_USE_NEWS="false")


@pytest.mark.asyncio
async def test_build_signal_uses_cryptomind_pipeline():
    service = FakeService({1: _candles(250)})
    agent = FakeAgent(_settings(), service, {"AAPL": Instrument(symbol="AAPL", instrument_id=1)})
    sig = await svc.build_signal(agent, "AAPL")
    assert sig is not None
    assert sig.symbol == "AAPL" and sig.instrument_id == 1 and sig.side == Side.BUY
    assert 0 <= sig.market_score <= 100
    assert service.calls == [(1, svc.CANDLE_INTERVAL, svc.CANDLE_COUNT)]


@pytest.mark.asyncio
async def test_build_signal_not_enough_candles_returns_none():
    service = FakeService({1: _candles(10)})
    agent = FakeAgent(_settings(), service, {"AAPL": Instrument(symbol="AAPL", instrument_id=1)})
    assert await svc.build_signal(agent, "AAPL") is None


@pytest.mark.asyncio
async def test_build_signal_unknown_symbol_returns_none():
    agent = FakeAgent(_settings(), FakeService({}), {})
    assert await svc.build_signal(agent, "TSLA") is None


@pytest.mark.asyncio
async def test_provider_loads_universe_and_isolates_failures():
    # id 1 = AAPL ok ; id 2 = XAUUSD -> get_candles lève -> ignoré sans casser le cycle
    service = FakeService({1: _candles(250)})
    agent = FakeAgent(_settings("AAPL,XAUUSD"), service, {})
    provider = svc.make_signal_provider(agent)
    signals = await provider()
    assert [s.symbol for s in signals] == ["AAPL"]
    assert set(agent.instruments_by_symbol) == {"AAPL", "XAUUSD"}
