"""Tests — watch/fng_source.py (alternative.me mocké par respx, cache 1 h, jamais d'exception)."""
from __future__ import annotations

import httpx
import pytest
import respx

from watch import fng_source
from watch.fng_source import FNG_URL, fetch_fng, parse_payload

PAYLOAD = {
    "name": "Fear and Greed Index",
    "data": [
        {"value": "25", "value_classification": "Extreme Fear", "timestamp": "1788998400", "time_until_update": "1"},
        {"value": "30", "value_classification": "Fear", "timestamp": "1788912000"},
        {"value": "oops", "value_classification": "Fear", "timestamp": "1788825600"},
        {"value": "60", "timestamp": "1788739200"},
    ],
}


@pytest.fixture(autouse=True)
def _clean_cache():
    fng_source.reset_cache()
    yield
    fng_source.reset_cache()


def test_parse_payload_is_tolerant():
    out = parse_payload(PAYLOAD)
    assert out[0] == {"date": "2026-09-10", "value": 25, "classification": "Extreme Fear"}
    assert out[1]["value"] == 30 and out[2] == {"date": "2026-09-07", "value": 60, "classification": "Greed"}
    assert len(out) == 3                                           # entrée "oops" ignorée
    assert parse_payload({"data": "nope"}) == [] and parse_payload(None) == []


@respx.mock
async def test_fetch_fng_uses_cache_for_an_hour():
    route = respx.get(FNG_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    async with httpx.AsyncClient() as client:
        first = await fetch_fng(client, limit=30)
        second = await fetch_fng(client, limit=30)
        assert first == second and first[0]["value"] == 25 and first[0]["date"] == "2026-09-10"
        assert route.call_count == 1                               # 2e appel servi par le cache
        assert route.calls[0].request.url.params["limit"] == "30"
        assert route.calls[0].request.url.params["format"] == "json"
        assert len(await fetch_fng(client, limit=2)) == 2           # sous-ensemble du cache, sans appel
        assert route.call_count == 1
        fng_source._cache["at"] -= fng_source.CACHE_TTL_S + 1      # cache périmé -> nouvel appel
        await fetch_fng(client, limit=30)
        assert route.call_count == 2
        await fetch_fng(client, limit=30, force=True)
        assert route.call_count == 3


@respx.mock
async def test_fetch_fng_never_raises_and_falls_back_to_cache():
    route = respx.get(FNG_URL).mock(return_value=httpx.Response(500, text="nope"))
    async with httpx.AsyncClient() as client:
        assert await fetch_fng(client) == []                        # aucune donnée, aucune exception
        route.mock(return_value=httpx.Response(200, json=PAYLOAD))
        assert len(await fetch_fng(client)) == 3
        route.mock(side_effect=httpx.ConnectError("down"))
        fng_source._cache["at"] -= fng_source.CACHE_TTL_S + 1
        stale = await fetch_fng(client)                             # réseau HS : dernier cache renvoyé
        assert len(stale) == 3 and stale[0]["value"] == 25
        route.mock(return_value=httpx.Response(200, json={"data": []}))
        assert len(await fetch_fng(client, force=True)) == 3        # réponse vide : cache conservé
        route.mock(return_value=httpx.Response(200, text="not json"))
        assert len(await fetch_fng(client, force=True)) == 3
