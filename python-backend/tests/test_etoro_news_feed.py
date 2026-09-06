"""Tests de etoro/news_feed.py : scoring lexical pur + sources HTTP mockées (respx)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from etoro import news_feed
from etoro.config import Settings
from etoro.news_feed import get_sentiment, score_headlines, score_items

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: AAPL News</title>
<item><title>Apple shares surge to record high after upgrade</title>
<link>https://example.com/1</link><pubDate>Sat, 05 Sep 2026 09:00:00 +0000</pubDate></item>
<item><title>Apple rally continues as analysts raise targets</title>
<link>https://example.com/2</link><pubDate>Fri, 04 Sep 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""


def make_settings(**overrides) -> Settings:
    base = {"ETORO_API_KEY": "test", "NEWS_API_KEY": "x", "ETORO_USER_KEY": None}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clear_cache():
    news_feed.clear_cache()
    yield
    news_feed.clear_cache()


# --- score_headlines (pur) ---------------------------------------------------------


def test_score_positive_titles():
    s = score_headlines(["Apple beats estimates, shares surge", "Analyst upgrade sparks record rally"])
    assert s == 1.0


def test_score_negative_titles():
    s = score_headlines(["Apple misses on revenue, stock plunges", "Downgrade after lawsuit and recall"])
    assert s == -1.0


def test_score_mixed_titles():
    s = score_headlines(["Shares surge despite lawsuit", "Guidance cut but record buyback announced"])
    assert -0.5 < s < 0.5


def test_score_empty():
    assert score_headlines([]) == 0.0
    assert score_headlines(["", "   "]) == 0.0


def test_score_neutral_title_is_zero():
    assert score_headlines(["Apple holds annual developer conference"]) == 0.0


def test_score_bounds_and_limit():
    titles = ["surge"] * 30 + ["plunge"] * 30  # seuls les 20 premiers comptent
    s = score_headlines(titles)
    assert s == 1.0


def test_score_items_fresh_weighs_double():
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    fresh = ("Stock surges on record profit", now - timedelta(hours=2))
    stale = ("Stock plunges after downgrade", now - timedelta(days=3))
    # +1 pondéré 2, -1 pondéré 1 => (2 - 1) / 3
    assert score_items([fresh, stale], now=now) == pytest.approx(1 / 3)
    # Sans date => poids 1
    assert score_items([("Stock surges", None), ("Stock plunges", None)], now=now) == 0.0


def test_symbol_mapping():
    assert news_feed.query_for("XAUUSD") == "gold price OR XAU/USD"
    assert news_feed.query_for("SPY") == "S&P 500"
    assert news_feed.query_for("tsla") == "TSLA"
    assert news_feed.yahoo_symbol_for("XAUUSD") == "GC=F"
    assert news_feed.yahoo_symbol_for("AAPL") == "AAPL"


# --- get_sentiment (HTTP mocké) ----------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_newsapi_positive():
    now = datetime.now(timezone.utc)
    route = respx.get(news_feed.NEWSAPI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "totalResults": 2,
                "articles": [
                    {"title": "Apple beats expectations, shares surge", "publishedAt": now.isoformat()},
                    {"title": "Record iPhone sales drive rally", "publishedAt": (now - timedelta(days=2)).isoformat()},
                ],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("AAPL", make_settings(), client)
    assert score == 1.0
    assert route.called
    req = route.calls.last.request
    assert req.headers["X-Api-Key"] == "x"
    assert "AAPL" in str(req.url)


@pytest.mark.asyncio
@respx.mock
async def test_newsapi_negative_and_cache():
    route = respx.get(news_feed.NEWSAPI_URL).mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "articles": [{"title": "Stock plunges after downgrade and lawsuit", "publishedAt": None}]},
        )
    )
    async with httpx.AsyncClient() as client:
        settings = make_settings()
        first = await get_sentiment("MSFT", settings, client)
        second = await get_sentiment("MSFT", settings, client)
    assert first == -1.0
    assert second == -1.0
    assert route.call_count == 1  # cache 15 min


@pytest.mark.asyncio
@respx.mock
async def test_fallback_rss_when_newsapi_fails():
    respx.get(news_feed.NEWSAPI_URL).mock(return_value=httpx.Response(500))
    rss = respx.get(news_feed.YAHOO_RSS_URL).mock(return_value=httpx.Response(200, text=RSS_XML))
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("AAPL", make_settings(), client)
    assert score == 1.0
    assert rss.called
    assert rss.calls.last.request.url.params["s"] == "AAPL"


@pytest.mark.asyncio
@respx.mock
async def test_rss_directly_when_no_newsapi_key_and_gold_symbol():
    newsapi = respx.get(news_feed.NEWSAPI_URL).mock(return_value=httpx.Response(200, json={"status": "ok", "articles": []}))
    rss = respx.get(news_feed.YAHOO_RSS_URL).mock(return_value=httpx.Response(200, text=RSS_XML))
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("XAUUSD", make_settings(NEWS_API_KEY=None), client)
    assert score == 1.0
    assert not newsapi.called
    assert rss.calls.last.request.url.params["s"] == "GC=F"


@pytest.mark.asyncio
@respx.mock
async def test_etoro_feed_used_first_when_user_key_present():
    settings = make_settings(ETORO_USER_KEY="ukey")
    etoro = respx.get(url__regex=r"https://public-api\.etoro\.com/api/v1/feeds/markets/NVDA.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "discussions": [
                    {"post": {"message": {"text": "NVDA surges, bullish momentum"}, "created": "2026-09-05T10:00:00Z"}},
                    {"post": {"message": {"text": ""}, "created": "2026-09-05T10:00:00Z"}},
                ]
            },
        )
    )
    newsapi = respx.get(news_feed.NEWSAPI_URL).mock(return_value=httpx.Response(200, json={"status": "ok", "articles": []}))
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("NVDA", settings, client)
    assert score == 1.0
    assert etoro.called
    assert not newsapi.called
    headers = etoro.calls.last.request.headers
    assert headers["x-api-key"] == "test"
    assert headers["x-user-key"] == "ukey"
    assert headers["x-request-id"]


@pytest.mark.asyncio
@respx.mock
async def test_none_on_network_error():
    respx.get(news_feed.NEWSAPI_URL).mock(side_effect=httpx.ConnectError("boom"))
    respx.get(news_feed.YAHOO_RSS_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("AAPL", make_settings(), client)
    assert score is None


@pytest.mark.asyncio
@respx.mock
async def test_none_on_garbage_responses():
    respx.get(news_feed.NEWSAPI_URL).mock(return_value=httpx.Response(200, text="not json"))
    respx.get(news_feed.YAHOO_RSS_URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    async with httpx.AsyncClient() as client:
        score = await get_sentiment("AAPL", make_settings(), client)
    assert score is None
