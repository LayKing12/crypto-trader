"""Tests — watch/smallcaps.py : sélection top 50-100 négociable sur Kraken, observation seule."""
from __future__ import annotations

import httpx
import pytest
import respx

from watch import smallcaps
from watch.config import WatchSettings
from watch.market_watch_service import MarketWatch
from watch.observation_log import ObservationLog

MARKETS = [
    {"symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1, "market_cap": 1},
    {"symbol": "usdt", "name": "Tether", "market_cap_rank": 3, "market_cap": 1},
    {"symbol": "inj", "name": "Injective", "market_cap_rank": 55, "market_cap": 1},
    {"symbol": "wbtc", "name": "Wrapped BTC", "market_cap_rank": 60, "market_cap": 1},
    {"symbol": "fdusd", "name": "First Digital USD", "market_cap_rank": 62, "market_cap": 1},
    {"symbol": "tia", "name": "Celestia", "market_cap_rank": 70, "market_cap": 1},
    {"symbol": "zzz", "name": "Pas sur Kraken", "market_cap_rank": 80, "market_cap": 1},
    {"symbol": "doge", "name": "Dogecoin", "market_cap_rank": 9, "market_cap": 1},
    {"symbol": "ldo", "name": "Lido DAO", "market_cap_rank": 99, "market_cap": 1},
    {"symbol": "far", "name": "Trop loin", "market_cap_rank": 101, "market_cap": 1},
]
KRAKEN = {"result": {
    "XXBTZUSD": {"wsname": "XBT/USD", "altname": "XBTUSD"},
    "INJUSD": {"wsname": "INJ/USD", "altname": "INJUSD"},
    "TIAUSD": {"wsname": "TIA/USD", "altname": "TIAUSD"},
    "LDOUSD": {"wsname": "LDO/USD", "altname": "LDOUSD"},
    "FARUSD": {"wsname": "FAR/USD", "altname": "FARUSD"},
    "TIAEUR": {"wsname": "TIA/EUR", "altname": "TIAEUR"},
    "INJUSD.d": {"wsname": "INJ/USD", "altname": "INJUSD.d"},
}}


@pytest.fixture(autouse=True)
def _reset():
    smallcaps.reset_cache()
    yield
    smallcaps.reset_cache()


def test_select_keeps_only_rank_window_on_kraken_without_stables_or_wrapped():
    out = smallcaps.select_smallcaps(MARKETS, smallcaps._kraken_usd_pairs(KRAKEN))
    assert [(m["symbol"], m["pair"], m["rank"]) for m in sorted(out, key=lambda m: m["rank"])] == [
        ("INJ", "INJUSD", 55), ("TIA", "TIAUSD", 70), ("LDO", "LDOUSD", 99)]


@pytest.mark.asyncio
@respx.mock
async def test_smallcap_pairs_uses_cache_and_survives_errors():
    cg = respx.get(smallcaps.COINGECKO_MARKETS_URL).mock(return_value=httpx.Response(200, json=MARKETS))
    respx.get(smallcaps.KRAKEN_ASSET_PAIRS_URL).mock(return_value=httpx.Response(200, json=KRAKEN))
    async with httpx.AsyncClient() as client:
        first = await smallcaps.smallcap_pairs(client)
        second = await smallcaps.smallcap_pairs(client)
    assert [m["pair"] for m in first] == ["INJUSD", "TIAUSD", "LDOUSD"] and second == first
    assert cg.call_count == 1
    smallcaps.reset_cache()
    respx.get(smallcaps.COINGECKO_MARKETS_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        assert await smallcaps.smallcap_pairs(client) == []


@pytest.mark.asyncio
async def test_market_watch_observes_smallcaps_read_only():
    prices = {"BTCUSD": [100.0, 100.5], "INJUSD": [10.0, 10.5]}

    async def kraken_price(pair):
        return prices[pair].pop(0)

    settings = WatchSettings(WATCH_ENABLED="true", WATCH_KRAKEN_PAIRS="BTCUSD", WATCH_ETORO_SYMBOLS="",
                             WATCH_MOVE_THRESHOLD_PCT="2.0", WATCH_LOG_PATH="", WATCH_SMALLCAPS_ENABLED="true")
    mw = MarketWatch(settings, ObservationLog(None), kraken_price_fn=kraken_price)
    mw.set_smallcaps([{"symbol": "INJ", "pair": "INJUSD", "rank": 55, "name": "Injective"}])
    await mw.tick()
    moves = await mw.tick()
    assert [o.symbol for o in moves] == ["INJUSD"]           # +5 % sur la small cap, +0,5 % sur BTC
    assert moves[0].detail.get("smallcap") is True and moves[0].detail.get("rank") == 55
    assert mw.status()["smallcaps"] == 1
