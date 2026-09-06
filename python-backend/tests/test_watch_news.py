"""Tests — watch/news_watch_service.py (RSS Yahoo mocké, CryptoPanic sans token ignoré)."""
from __future__ import annotations

import httpx
import pytest
import respx

from watch.config import WatchSettings
from watch.news_watch_service import NewsWatch
from watch.observation_log import ObservationLog

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Yahoo</title>
<item><title>Apple beats estimates as iPhone sales surge</title><link>https://x/1</link><pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Weather report for Brussels</title><link>https://x/2</link><pubDate>Mon, 01 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def _settings(**kw) -> WatchSettings:
    base = dict(WATCH_ENABLED="true", WATCH_KRAKEN_PAIRS="", WATCH_ETORO_SYMBOLS="AAPL",
                WATCH_NEWS_SOURCES="yahoo,cryptopanic", WATCH_LOG_PATH="")
    base.update(kw)
    return WatchSettings(**base)


@pytest.mark.asyncio
@respx.mock
async def test_rss_filtered_by_ticker_and_deduplicated():
    route = respx.get(url__regex=r"https://feeds\.finance\.yahoo\.com/.*").mock(
        return_value=httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
    )
    log = ObservationLog(None)
    async with httpx.AsyncClient() as client:
        nw = NewsWatch(_settings(), log, client)
        first = await nw.tick()
        second = await nw.tick()
    assert route.called
    assert len(first) == 1 and first[0].kind == "news" and first[0].symbol == "AAPL"
    assert "Apple" in first[0].title and first[0].detail.get("url") == "https://x/1"
    assert second == []  # dédoublonnage au 2e poll
    assert len(log.recent(kind="news")) == 1


@pytest.mark.asyncio
@respx.mock
async def test_cryptopanic_ignored_without_token():
    respx.get(url__regex=r"https://feeds\.finance\.yahoo\.com/.*").mock(return_value=httpx.Response(200, text=RSS))
    cp = respx.get(url__regex=r"https://cryptopanic\.com/.*").mock(return_value=httpx.Response(200, json={"results": []}))
    log = ObservationLog(None)
    async with httpx.AsyncClient() as client:
        nw = NewsWatch(_settings(WATCH_KRAKEN_PAIRS="BTCUSD"), log, client)
        await nw.tick()
    assert not cp.called


@pytest.mark.asyncio
@respx.mock
async def test_network_error_does_not_raise():
    respx.get(url__regex=r"https://feeds\.finance\.yahoo\.com/.*").mock(side_effect=httpx.ConnectError("boom"))
    log = ObservationLog(None)
    async with httpx.AsyncClient() as client:
        nw = NewsWatch(_settings(), log, client)
        obs = await nw.tick()
    assert obs == [] and nw.last_error is not None
