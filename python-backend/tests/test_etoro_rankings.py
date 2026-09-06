"""Tests de etoro.rankings (HTTP simulé avec respx)."""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from etoro import rankings
from etoro.config import Settings
from etoro.models import Side

BASE = "https://public-api.etoro.com"
RANKINGS_RE = re.compile(rf"{re.escape(BASE)}/api/v2/portfolios/rankings.*")
PORTFOLIO_RE = re.compile(rf"{re.escape(BASE)}/api/v1/user-info/people/(?P<username>[^/]+)/portfolio/live")


def trader(username: str, gain: float, dd: float, pm: float = 75.0, first_activity: str = "2020-01-01T00:00:00Z", **extra):
    row = {
        "cid": abs(hash(username)) % 10_000,
        "username": username,
        "gain": gain,
        "dailyDD": -dd / 2,
        "weeklyDD": -dd,
        "peakToValley": -dd,
        "riskScore": 4,
        "maxDailyRiskScore": 5,
        "profitableMonthsPct": pm,
        "activeWeeks": 200,
        "weeksSinceRegistration": 300,
        "firstActivity": first_activity,
        "copiers": 100,
        "popularInvestor": True,
    }
    row.update(extra)
    return row


def rankings_payload(rows):
    return {"results": rows, "pagination": {"page": 1, "pageSize": 100, "totalItems": len(rows), "hasNext": False}}


def portfolio_payload(instrument_ids, is_buy=True):
    return {
        "realizedCreditPct": 0,
        "unrealizedCreditPct": 0,
        "positions": [{"positionId": i, "instrumentId": iid, "isBuy": is_buy, "investmentPct": 5.0} for i, iid in enumerate(instrument_ids)],
        "socialTrades": [],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    rankings.clear_cache()
    yield
    rankings.clear_cache()


@pytest.fixture
def settings():
    return Settings(ETORO_API_KEY="test", ETORO_USER_KEY="user", ETORO_RANKINGS_TOP_N=4)


GOOD_ROWS = [
    trader("alice", gain=30.0, dd=5.0),
    trader("bob", gain=25.0, dd=8.0),
    trader("carol", gain=40.0, dd=12.0),
    trader("dave", gain=20.0, dd=3.0),
    trader("risky", gain=90.0, dd=20.0),  # exclu : DD > 15 %
    trader("newbie", gain=50.0, dd=4.0, first_activity="2026-06-01T00:00:00Z"),  # exclu : < 12 mois
    trader("erratic", gain=35.0, dd=6.0, pm=40.0),  # exclu : mois profitables < 60 %
]


@pytest.mark.asyncio
@respx.mock
async def test_filters_exclude_high_drawdown_short_history_and_low_profitable_months(settings):
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    async with httpx.AsyncClient() as client:
        top = await rankings.get_top_traders(settings, client)
    names = {t.username for t in top}
    assert names == {"alice", "bob", "carol", "dave"}
    assert "risky" not in names and "newbie" not in names and "erratic" not in names
    # Tri par régularité : alice (30/6 = 5.0) et dave (20/4 = 5.0) devant carol (40/13 = 3.08)
    # malgré son gain brut le plus élevé ; bob (25/9 = 2.78) ferme la marche.
    assert [t.username for t in top] == ["alice", "dave", "carol", "bob"]
    assert all(t.score > 0 for t in top)


@pytest.mark.asyncio
@respx.mock
async def test_auth_headers_and_query_params(settings):
    route = respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    async with httpx.AsyncClient() as client:
        await rankings.get_top_traders(settings, client)
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "test"
    assert req.headers["x-user-key"] == "user"
    assert len(req.headers["x-request-id"]) == 36
    assert req.url.params["period"] == rankings.RANKINGS_PERIOD
    assert req.url.params["pageSize"] == "100"


@pytest.mark.asyncio
@respx.mock
async def test_confirmation_ratio(settings):
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    exposure = {"alice": [1001, 2002], "bob": [1001], "carol": [3003], "dave": []}

    def portfolio(request, username):
        return httpx.Response(200, json=portfolio_payload(exposure[username]))

    respx.get(PORTFOLIO_RE).mock(side_effect=portfolio)
    async with httpx.AsyncClient() as client:
        ratio = await rankings.get_confirmation(1001, settings, client)
        other = await rankings.get_confirmation(9999, settings, client)
    assert ratio == pytest.approx(0.5)  # alice + bob sur 4 inspectés
    assert other == 0.0


@pytest.mark.asyncio
@respx.mock
async def test_confirmation_respects_side_when_given(settings):
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    # tout le monde est long sur 1001
    respx.get(PORTFOLIO_RE).mock(return_value=httpx.Response(200, json=portfolio_payload([1001], is_buy=True)))
    async with httpx.AsyncClient() as client:
        assert await rankings.get_confirmation(1001, settings, client, side=Side.BUY) == pytest.approx(1.0)
        assert await rankings.get_confirmation(1001, settings, client, side=Side.SELL) == pytest.approx(0.0)


@pytest.mark.asyncio
@respx.mock
async def test_unreadable_portfolios_are_excluded_from_denominator(settings):
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))

    def portfolio(request, username):
        if username in ("carol", "dave"):
            return httpx.Response(404, json={"message": "private"})
        return httpx.Response(200, json=portfolio_payload([1001] if username == "alice" else [5]))

    respx.get(PORTFOLIO_RE).mock(side_effect=portfolio)
    async with httpx.AsyncClient() as client:
        ratio = await rankings.get_confirmation(1001, settings, client)
    assert ratio == pytest.approx(0.5)  # alice / (alice, bob)


@pytest.mark.asyncio
@respx.mock
async def test_returns_none_on_server_error(settings):
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(500, text="boom"))
    async with httpx.AsyncClient() as client:
        assert await rankings.get_confirmation(1001, settings, client) is None
        assert await rankings.get_top_traders(settings, client) == []


@pytest.mark.asyncio
@respx.mock
async def test_returns_none_on_network_error_and_all_portfolios_unreadable(settings):
    respx.get(RANKINGS_RE).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        assert await rankings.get_confirmation(1001, settings, client) is None

    rankings.clear_cache()
    respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    respx.get(PORTFOLIO_RE).mock(return_value=httpx.Response(403, json={"message": "forbidden"}))
    async with httpx.AsyncClient() as client:
        assert await rankings.get_confirmation(1001, settings, client) is None


@pytest.mark.asyncio
@respx.mock
async def test_cache_is_used_on_second_call(settings):
    rank_route = respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    port_route = respx.get(PORTFOLIO_RE).mock(return_value=httpx.Response(200, json=portfolio_payload([1001])))
    async with httpx.AsyncClient() as client:
        first = await rankings.get_confirmation(1001, settings, client)
        assert rank_route.call_count == 1
        assert port_route.call_count == 4
        second = await rankings.get_confirmation(1001, settings, client)
        third = await rankings.get_confirmation(2002, settings, client)  # autre instrument, mêmes portfolios
    assert first == second == pytest.approx(1.0)
    assert third == 0.0
    assert rank_route.call_count == 1
    assert port_route.call_count == 4


@pytest.mark.asyncio
@respx.mock
async def test_cache_expires(settings, monkeypatch):
    rank_route = respx.get(RANKINGS_RE).mock(return_value=httpx.Response(200, json=rankings_payload(GOOD_ROWS)))
    async with httpx.AsyncClient() as client:
        await rankings.get_top_traders(settings, client)
        monkeypatch.setattr(rankings.time, "monotonic", lambda: 10_000_000.0)
        await rankings.get_top_traders(settings, client)
    assert rank_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_pagination_follows_has_next(settings):
    page1 = {"results": [trader(f"p1_{i}", 10.0, 2.0) for i in range(100)], "pagination": {"page": 1, "pageSize": 100, "hasNext": True}}
    page2 = {"results": [trader("p2_star", 50.0, 1.0)], "pagination": {"page": 2, "pageSize": 100, "hasNext": False}}
    route = respx.get(RANKINGS_RE).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    async with httpx.AsyncClient() as client:
        top = await rankings.get_top_traders(settings, client)
    assert route.call_count == 2
    assert top[0].username == "p2_star"


def test_from_api_accepts_legacy_pascal_case():
    t = rankings.TraderRank.from_api({"CID": 7, "UserName": "legacy", "Gain": 12.5, "WeeklyDD": -9.0, "ProfitableMonthsPct": 70})
    assert t.cid == 7 and t.username == "legacy" and t.gain == 12.5
    assert t.max_drawdown == 9.0 and t.profitable_months_pct == 70


def test_get_confirmation_is_in_contract_shape():
    import inspect

    sig = inspect.signature(rankings.get_confirmation)
    assert list(sig.parameters)[:3] == ["instrument_id", "settings", "client"]
    assert inspect.iscoroutinefunction(rankings.get_confirmation)
