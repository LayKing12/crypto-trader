"""Tests du client eToro avec mocks HTTP respx (aucun appel réseau réel)."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

from etoro.config import Settings
from etoro.etoro_service import ENDPOINTS, EtoroApiError, EtoroService
from etoro.models import OrderRequest, Side

BASE = "https://etoro.test"


def make_settings(**overrides) -> Settings:
    values = dict(
        ETORO_API_KEY="test-api-key",
        ETORO_USER_KEY="test-user-key",
        ETORO_MODE="demo",
        ETORO_BASE_URL_DEMO=BASE,
        ETORO_BASE_URL_REAL=BASE,
        ETORO_TIMEOUT_S=2.0,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_service(**kwargs) -> EtoroService:
    return EtoroService(make_settings(), backoff_base_s=0.0, position_poll_delay_s=0.0,
                        position_poll_attempts=2, **kwargs)


RATES_PAYLOAD = {
    "rates": [
        {"instrumentID": 1001, "bid": 190.10, "ask": 190.20, "date": "2026-09-05T10:00:00.123Z"},
        {"instrumentID": 14, "bid": 2400.5, "ask": 2401.0, "date": "2026-09-05T10:00:01Z"},
    ]
}

PORTFOLIO_EMPTY = {"clientPortfolio": {"credit": 10000.0, "positions": [], "orders": []}}

PORTFOLIO_WITH_POSITION = {
    "clientPortfolio": {
        "credit": 9000.0,
        "unrealizedPnL": 25.0,
        "positions": [
            {
                "positionID": 2150896073,
                "instrumentID": 1001,
                "isBuy": True,
                "amount": 1000.0,
                "leverage": 1,
                "openRate": 190.2,
                "stopLossRate": 186.4,
                "takeProfitRate": 197.8,
                "openDateTime": "2026-09-05T10:00:05Z",
                "unrealizedPnL": 25.0,
            }
        ],
    }
}


def make_order(**overrides) -> OrderRequest:
    values = dict(instrument_id=1001, side=Side.BUY, amount=1000.0, leverage=1, entry_rate=190.2,
                  stop_loss_rate=186.4, take_profit_rate=197.8, score=80.0)
    values.update(overrides)
    return OrderRequest(**values)


@pytest.mark.asyncio
async def test_auth_headers_present():
    service = make_service()
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["rates"]).respond(200, json=RATES_PAYLOAD)
        await service.get_quote(1001)
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "test-api-key"
    assert req.headers["x-user-key"] == "test-user-key"
    uuid.UUID(req.headers["x-request-id"])  # doit être un uuid valide
    assert "x-api-key" not in str(req.url)
    await service.aclose()


@pytest.mark.asyncio
async def test_quote_mapping():
    service = make_service()
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["rates"]).respond(200, json=RATES_PAYLOAD)
        quotes = await service.get_quotes([1001, 14])
        gold = await service.get_quote(14)
    assert route.calls.last.request.url.params["instrumentIds"] == "14"
    assert [q.instrument_id for q in quotes] == [1001, 14]
    assert quotes[0].bid == 190.10 and quotes[0].ask == 190.20
    assert quotes[0].timestamp.isoformat() == "2026-09-05T10:00:00.123000+00:00"
    assert gold.bid == 2400.5 and gold.timestamp.tzinfo is not None
    await service.aclose()


@pytest.mark.asyncio
async def test_open_position_refuses_without_sl_tp():
    service = make_service()
    async with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.post(ENDPOINTS["open_by_amount_demo"]).respond(200, json={})
        with pytest.raises(ValueError):
            await service.open_position(make_order(stop_loss_rate=None))
        with pytest.raises(ValueError):
            await service.open_position(make_order(take_profit_rate=None))
    assert not route.called
    await service.aclose()


@pytest.mark.asyncio
async def test_retry_on_503_then_success():
    service = make_service()
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["rates"])
        route.side_effect = [httpx.Response(503, json={"error": "down"}), httpx.Response(200, json=RATES_PAYLOAD)]
        quote = await service.get_quote(1001)
    assert route.call_count == 2
    assert quote.ask == 190.20
    # même x-request-id sur les deux tentatives (traçabilité)
    ids = {c.request.headers["x-request-id"] for c in route.calls}
    assert len(ids) == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    service = make_service()
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["rates"]).respond(503, json={"error": "down"})
        with pytest.raises(EtoroApiError) as exc:
            await service.get_quote(1001)
    assert route.call_count == 3
    assert exc.value.status_code == 503
    await service.aclose()


@pytest.mark.asyncio
async def test_401_raises_etoro_api_error_without_retry():
    service = make_service()
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["portfolio_demo"]).respond(401, json={"message": "unauthorized"})
        with pytest.raises(EtoroApiError) as exc:
            await service.get_open_positions()
    assert route.call_count == 1
    assert exc.value.status_code == 401
    assert exc.value.payload == {"message": "unauthorized"}
    assert "test-api-key" not in str(exc.value)
    await service.aclose()


@pytest.mark.asyncio
async def test_open_position_success_resolves_position_id():
    service = make_service()
    order_payload = {
        "orderForOpen": {"instrumentID": 1001, "amount": 1000.0, "isBuy": True, "leverage": 1,
                         "stopLossRate": 186.4, "takeProfitRate": 197.8, "orderID": 13906629,
                         "statusID": 1, "openDateTime": "2026-09-05T10:00:04Z"},
        "token": "43ceb769-cff6-45ec-8ad7-292b7401353f",
    }
    async with respx.mock(base_url=BASE) as mock:
        portfolio = mock.get(ENDPOINTS["portfolio_demo"])
        portfolio.side_effect = [httpx.Response(200, json=PORTFOLIO_EMPTY),
                                 httpx.Response(200, json=PORTFOLIO_WITH_POSITION)]
        open_route = mock.post(ENDPOINTS["open_by_amount_demo"]).respond(200, json=order_payload)
        position = await service.open_position(make_order())
    body = json.loads(open_route.calls.last.request.read())
    assert body["InstrumentID"] == 1001 and body["IsBuy"] is True and body["Amount"] == 1000.0
    assert body["StopLossRate"] == 186.4 and body["TakeProfitRate"] == 197.8
    assert body["IsNoStopLoss"] is False and body["IsNoTakeProfit"] is False
    assert position.position_id == "2150896073"
    assert position.side == Side.BUY and position.stop_loss_rate == 186.4 and position.take_profit_rate == 197.8
    await service.aclose()


@pytest.mark.asyncio
async def test_open_position_rejects_when_api_drops_sl_tp():
    service = make_service()
    order_payload = {"orderForOpen": {"orderID": 1, "statusID": 1, "stopLossRate": 0, "takeProfitRate": 197.8}}
    async with respx.mock(base_url=BASE) as mock:
        mock.get(ENDPOINTS["portfolio_demo"]).respond(200, json=PORTFOLIO_EMPTY)
        mock.post(ENDPOINTS["open_by_amount_demo"]).respond(200, json=order_payload)
        with pytest.raises(EtoroApiError):
            await service.open_position(make_order())
    await service.aclose()


@pytest.mark.asyncio
async def test_real_mode_uses_real_paths_and_lock():
    locked = EtoroService(make_settings(ETORO_MODE="real"), backoff_base_s=0.0)
    with pytest.raises(RuntimeError):
        await locked.open_position(make_order())
    await locked.aclose()

    service = EtoroService(make_settings(ETORO_MODE="real", ETORO_CREDENTIALS_ROTATED="true"), backoff_base_s=0.0)
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["portfolio_real"]).respond(200, json=PORTFOLIO_WITH_POSITION)
        positions = await service.get_open_positions()
    assert route.called and positions[0].position_id == "2150896073"
    await service.aclose()


@pytest.mark.asyncio
async def test_close_position_and_balance():
    service = make_service()
    close_payload = {"orderForClose": {"positionID": 2150896073, "instrumentID": 1001, "orderID": 13904638,
                                       "statusID": 1, "lastUpdate": "2026-09-05T11:00:00Z"},
                     "token": "5fe065bc-f6f9-4897-a2ce-c4fccef73ff8"}
    async with respx.mock(base_url=BASE) as mock:
        mock.get(ENDPOINTS["portfolio_demo"]).respond(200, json=PORTFOLIO_WITH_POSITION)
        close_route = mock.post(ENDPOINTS["close_position_demo"].format(position_id="2150896073")).respond(
            200, json=close_payload)
        closed = await service.close_position("2150896073")
        equity = await service.get_account_balance()
    assert json.loads(close_route.calls.last.request.read()) == {"InstrumentID": 1001, "UnitsToDeduct": None}
    assert closed.instrument_id == 1001 and closed.realized_pnl == 25.0
    assert closed.closed_at.isoformat() == "2026-09-05T11:00:00+00:00"
    assert equity == pytest.approx(9000.0 + 1000.0 + 25.0)
    await service.aclose()


@pytest.mark.asyncio
async def test_get_closed_position_from_trade_history():
    service = make_service()
    history = {"trades": [
        {"positionID": 1, "instrumentID": 14, "netProfit": -3.5, "closeDateTime": "2026-09-04T12:00:00Z"},
        {"positionID": 2150896073, "instrumentID": 1001, "netProfit": 41.2, "closeDateTime": "2026-09-05T12:00:00Z"},
    ]}
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["trade_history"]).respond(200, json=history)
        closed = await service.get_closed_position("2150896073")
        with pytest.raises(EtoroApiError):
            await service.get_closed_position("999")
    assert "minDate" in route.calls.last.request.url.params
    assert closed.instrument_id == 1001 and closed.realized_pnl == 41.2
    assert closed.closed_at.isoformat() == "2026-09-05T12:00:00+00:00"
    await service.aclose()


@pytest.mark.asyncio
async def test_get_instruments_with_gold_alias():
    service = make_service()

    def search_handler(request: httpx.Request) -> httpx.Response:
        sym = request.url.params["internalSymbolFull"]
        catalogue = {
            "AAPL": {"instrumentId": 1001, "internalSymbolFull": "AAPL", "displayname": "Apple", "instrumentTypeID": 5},
            "GOLD": {"instrumentId": 14, "internalSymbolFull": "GOLD", "displayname": "Gold", "instrumentTypeID": 2},
        }
        items = [catalogue[sym]] if sym in catalogue else []
        return httpx.Response(200, json={"page": 1, "pageSize": 10, "totalItems": len(items), "items": items})

    async with respx.mock(base_url=BASE) as mock:
        mock.get(ENDPOINTS["search"]).mock(side_effect=search_handler)
        instruments = await service.get_instruments(["AAPL", "XAUUSD", "ZZZZ"])
    by_symbol = {i.symbol: i for i in instruments}
    assert set(by_symbol) == {"AAPL", "XAUUSD"}
    assert by_symbol["XAUUSD"].instrument_id == 14 and by_symbol["XAUUSD"].asset_class == "commodity"
    assert by_symbol["AAPL"].asset_class == "stock"
    await service.aclose()


@pytest.mark.asyncio
async def test_health_and_injected_client():
    client = httpx.AsyncClient()
    service = EtoroService(make_settings(), client=client, backoff_base_s=0.0)
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(ENDPOINTS["portfolio_demo"])
        route.side_effect = [httpx.Response(200, json=PORTFOLIO_EMPTY), httpx.Response(500)]
        assert await service.health() is True
        assert await service.health() is False
    await service.aclose()
    assert not client.is_closed  # client injecté : non fermé par le service
    await client.aclose()
