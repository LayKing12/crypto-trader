"""Tests — routes PR 4 de watch/api.py (alertes, règles, portefeuille, allocation, fng)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from watch import api as watch_api
from watch.config import WatchSettings
from watch.observation_log import ObservationLog
from watch.rules.models import WatchAlert, WatchBase
from watch.rules.store import RulesStore

SNAPSHOT = {
    "at": "2026-09-06T12:00:00+00:00", "total_usd": 10000.0,
    "categories": [
        {"category": "crypto", "value_usd": 4800.0, "actual_pct": 48.0, "target_pct": 40.0, "delta_points": 8.0},
        {"category": "stocks", "value_usd": 4200.0, "actual_pct": 42.0, "target_pct": None, "delta_points": None},
        {"category": "gold_miners", "value_usd": 0.0, "actual_pct": 0.0, "target_pct": None, "delta_points": None},
        {"category": "cash", "value_usd": 1000.0, "actual_pct": 10.0, "target_pct": None, "delta_points": None},
    ],
    "positions": {},
}
FNG = [{"date": "2026-09-06", "value": 25, "classification": "Extreme Fear"},
       {"date": "2026-09-05", "value": 30, "classification": "Fear"}]


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(WatchBase.metadata.create_all)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = RulesStore(sf)

    async def portfolio_fn():
        return SNAPSHOT

    async def fng_fn():
        return FNG

    watch_api.configure(ObservationLog(None), settings=WatchSettings(WATCH_ENABLED="true", WATCH_LOG_PATH=""),
                        rules_store=store, portfolio_fn=portfolio_fn, fng_fn=fng_fn)
    app = FastAPI()
    app.include_router(watch_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, store
    watch_api.reset()
    await engine.dispose()


TP_BODY = {"family": "take_profit", "name": "AAPL paliers", "enabled": True, "symbol": "AAPL",
           "params": {"levels": [{"price": 250, "sell_pct": 25}], "pru": 190}}


# ------------------------------------------------------------------ règles

async def test_rules_crud_with_422(ctx):
    client, _ = ctx
    r = await client.post("/watch/rules", json=TP_BODY)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["family"] == "take_profit" and rule["symbol"] == "AAPL" and rule["state"]["condition_active"] is False
    assert set(rule) >= {"id", "family", "name", "enabled", "symbol", "params", "state", "created_at", "updated_at"}

    body = (await client.get("/watch/rules")).json()
    assert body["rules"][0]["id"] == rule["id"]

    r = await client.put(f"/watch/rules/{rule['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["params"] == rule["params"]

    # 422 : params invalides pour la famille
    bad = dict(TP_BODY, params={"levels": [{"sell_pct": 25}]})            # ni price ni multiple_of_pru
    assert (await client.post("/watch/rules", json=bad)).status_code == 422
    bad = {"family": "allocation_drift", "name": "x", "params": {"category": "bonds", "target_pct": 40}}
    assert (await client.post("/watch/rules", json=bad)).status_code == 422
    bad = {"family": "sentiment_zone", "name": "x", "params": {"zone": "fear", "consecutive_days": 0}}
    assert (await client.post("/watch/rules", json=bad)).status_code == 422
    bad = {"family": "nope", "name": "x", "params": {}}
    assert (await client.post("/watch/rules", json=bad)).status_code == 422
    bad = dict(TP_BODY, symbol=None)                                     # take_profit sans symbole
    assert (await client.post("/watch/rules", json=bad)).status_code == 422
    r = await client.put(f"/watch/rules/{rule['id']}", json={"params": {"levels": [{"price": 0, "sell_pct": 5}]}})
    assert r.status_code == 422
    assert (await client.put("/watch/rules/missing", json={"name": "x"})).status_code == 404

    assert (await client.delete(f"/watch/rules/{rule['id']}")).json() == {"deleted": True}
    assert (await client.delete(f"/watch/rules/{rule['id']}")).status_code == 404
    assert (await client.get("/watch/rules")).json()["rules"] == []


# ------------------------------------------------------------------ alertes

async def test_alerts_list_and_actions(ctx):
    client, store = ctx
    a = await store.create_alert({"rule_id": "r", "family": "take_profit", "rule_name": "AAPL", "symbol": "AAPL",
                                  "title": "palier 1", "detail": {"level": 1}})
    b = await store.create_alert({"rule_id": "r", "family": "sentiment_zone", "rule_name": "peur", "symbol": None,
                                  "title": "peur extrême", "detail": {}})
    body = (await client.get("/watch/alerts")).json()
    assert body["count"] == 2 and body["alerts"][0]["id"] == b["id"]      # plus récent en premier
    assert set(body["alerts"][0]) >= {"id", "rule_id", "family", "rule_name", "symbol", "title", "detail", "status",
                                      "created_at", "acted_at", "postponed_until", "telegram_message_id"}
    assert (await client.get("/watch/alerts?limit=1")).json()["count"] == 1

    r = await client.post(f"/watch/alerts/{a['id']}/action", json={"action": "executed"})
    assert r.status_code == 200 and r.json()["status"] == "executed" and r.json()["acted_at"]
    r = await client.post(f"/watch/alerts/{a['id']}/action", json={"action": "ignored"})
    assert r.status_code == 409
    assert (await client.post("/watch/alerts/missing/action", json={"action": "ignored"})).status_code == 404
    assert (await client.post(f"/watch/alerts/{b['id']}/action", json={"action": "snooze"})).status_code == 422

    r = await client.post(f"/watch/alerts/{b['id']}/action", json={"action": "postponed", "postpone_hours": 2})
    assert r.status_code == 200 and r.json()["status"] == "postponed" and r.json()["postponed_until"]
    assert (await client.get("/watch/alerts?status=pending")).json()["count"] == 0
    assert (await client.get("/watch/alerts?status=all")).json()["count"] == 2
    assert (await client.get("/watch/alerts?status=weird")).status_code == 422

    # postponed expiré -> pending via GET /watch/alerts
    async with store._sf() as session:
        row = await session.get(WatchAlert, b["id"])
        row.postponed_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    body = (await client.get("/watch/alerts?status=pending")).json()
    assert body["count"] == 1 and body["alerts"][0]["id"] == b["id"] and body["alerts"][0]["postponed_until"] is None


# ------------------------------------------------------------------ portefeuille / allocation / fng

async def test_portfolio_history_allocation_and_fng(ctx):
    client, store = ctx
    now = datetime.now(UTC)
    await store.add_snapshot({"total_usd": 900, "categories": [], "positions": {}}, at=now - timedelta(days=20))
    await store.add_snapshot({"total_usd": 1000, "categories": [], "positions": {}}, at=now - timedelta(days=2))

    body = (await client.get("/watch/portfolio/history?days=30")).json()
    assert body["days"] == 30 and [p["value_usd"] for p in body["points"]] == [900.0, 1000.0]
    assert set(body["points"][0]) == {"at", "value_usd"}
    assert (await client.get("/watch/portfolio/history?days=7")).json()["points"] == [{"at": body["points"][1]["at"], "value_usd": 1000.0}]
    assert (await client.get("/watch/portfolio/history?days=9999")).json()["days"] == 365

    alloc = (await client.get("/watch/allocation")).json()
    assert alloc["total_usd"] == 10000.0
    crypto = next(c for c in alloc["categories"] if c["category"] == "crypto")
    assert crypto["actual_pct"] == 48.0 and crypto["target_pct"] == 40.0 and crypto["delta_points"] == 8.0
    assert set(crypto) == {"category", "value_usd", "actual_pct", "target_pct", "delta_points"}

    fng = (await client.get("/watch/fng")).json()
    assert fng["value"] == 25 and fng["classification"] == "Extreme Fear" and fng["date"] == "2026-09-06"
    assert fng["history"] == FNG


async def test_allocation_falls_back_to_last_snapshot_and_fng_to_table(ctx):
    client, store = ctx
    watch_api._deps.portfolio_fn = None
    watch_api._deps.fng_fn = None
    assert (await client.get("/watch/allocation")).json()["available"] is False
    await store.add_snapshot(SNAPSHOT)
    alloc = (await client.get("/watch/allocation")).json()
    assert alloc["available"] is True and alloc["total_usd"] == 10000.0 and len(alloc["categories"]) == 4

    assert (await client.get("/watch/fng")).json()["value"] is None
    await store.upsert_daily(FNG)
    fng = (await client.get("/watch/fng")).json()
    assert fng["value"] == 25 and len(fng["history"]) == 2


async def test_routes_without_store():
    watch_api.configure(ObservationLog(None))
    app = FastAPI()
    app.include_router(watch_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/watch/alerts")).json() == {"alerts": [], "count": 0, "available": False}
        assert (await client.get("/watch/rules")).json()["rules"] == []
        assert (await client.post("/watch/rules", json=TP_BODY)).status_code == 503
        assert (await client.post("/watch/alerts/x/action", json={"action": "executed"})).status_code == 503
        assert (await client.get("/watch/portfolio/history")).json()["points"] == []
        assert (await client.get("/watch/allocation")).json()["categories"] == []
        assert (await client.get("/watch/fng")).json()["history"] == []
        st = (await client.get("/watch/status")).json()
        assert st["rules"]["available"] is False
    watch_api.reset()
