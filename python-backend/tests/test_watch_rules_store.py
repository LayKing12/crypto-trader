"""Tests — watch/rules/store.py sur sqlite+aiosqlite en mémoire."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from watch.rules import store as store_mod
from watch.rules.models import WatchAlert, WatchBase
from watch.rules.store import AlertAlreadyHandled, AlertNotFound, RuleNotFound, RulesStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(WatchBase.metadata.create_all)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield RulesStore(sf)
    store_mod.configure(None)
    await engine.dispose()


TP = {"family": "take_profit", "name": "AAPL paliers", "symbol": "aapl",
      "params": {"levels": [{"price": 250, "sell_pct": 25}, {"multiple_of_pru": 1.5, "sell_pct": 50}], "pru": 190}}


async def _alert(store, title="AAPL a franchi le palier 1 (250.00) : vendre 25 %"):
    return await store.create_alert({"rule_id": "r1", "family": "take_profit", "rule_name": "AAPL",
                                     "symbol": "AAPL", "title": title, "detail": {"level": 1}})


# ------------------------------------------------------------------ règles

async def test_rules_crud_and_validation(store):
    rule = await store.create_rule(TP)
    assert rule["id"] and rule["symbol"] == "AAPL" and rule["enabled"] is True
    assert rule["state"] == {"condition_active": False, "last_fired_at": None, "consecutive_days": 0,
                             "levels_fired": [False, False]}
    assert rule["params"]["levels"][0] == {"price": 250.0, "multiple_of_pru": None, "sell_pct": 25.0}
    assert (await store.list_rules())[0]["id"] == rule["id"]
    assert (await store.get_rule(rule["id"]))["name"] == "AAPL paliers"
    assert await store.get_rule("missing") is None

    updated = await store.update_rule(rule["id"], {"enabled": False, "name": "renommée"})
    assert updated["enabled"] is False and updated["name"] == "renommée" and updated["params"] == rule["params"]
    assert await store.list_rules(enabled=True) == []

    await store.set_rule_state(rule["id"], {"condition_active": True, "levels_fired": [True, False]})
    assert (await store.get_rule(rule["id"]))["state"]["levels_fired"] == [True, False]
    # changement de params -> état réarmé
    updated = await store.update_rule(rule["id"], {"params": {"levels": [{"price": 300, "sell_pct": 10}]}})
    assert updated["state"]["levels_fired"] == [False] and updated["state"]["condition_active"] is False

    with pytest.raises(ValueError):
        await store.create_rule({"family": "take_profit", "name": "x", "symbol": "AAPL", "params": {"levels": []}})
    with pytest.raises(ValueError):
        await store.create_rule({"family": "allocation_drift", "name": "x", "params": {"category": "bonds", "target_pct": 10}})
    with pytest.raises(ValueError):
        await store.create_rule({"family": "sentiment_zone", "name": "x", "params": {"zone": "neutral"}})
    with pytest.raises(ValueError):
        await store.update_rule(rule["id"], {"params": {"levels": [{"price": -1, "sell_pct": 10}]}})
    with pytest.raises(RuleNotFound):
        await store.update_rule("missing", {"name": "x"})

    assert await store.delete_rule(rule["id"]) is True
    assert await store.delete_rule(rule["id"]) is False
    assert await store.list_rules() == []


async def test_allocation_and_sentiment_defaults(store):
    a = await store.create_rule({"family": "allocation_drift", "name": "crypto 40",
                                 "params": {"category": "crypto", "target_pct": 40}})
    assert a["params"] == {"category": "crypto", "target_pct": 40.0, "threshold_points": 5.0, "min_rebalance_usd": 100.0}
    s = await store.create_rule({"family": "sentiment_zone", "name": "peur", "params": {"zone": "extreme_fear"}})
    assert s["params"] == {"zone": "extreme_fear", "consecutive_days": 3}
    assert [r["family"] for r in await store.list_rules(family="sentiment_zone")] == ["sentiment_zone"]


# ------------------------------------------------------------------ alertes

async def test_alert_actions_and_conflict(store):
    a = await _alert(store)
    assert a["status"] == "pending" and a["acted_at"] is None and a["telegram_message_id"] is None
    await store.set_alert_telegram_id(a["id"], 42)
    b = await _alert(store, "deuxième")
    c = await _alert(store, "troisième")

    pending = await store.list_alerts("pending")
    assert [x["title"] for x in pending] == ["troisième", "deuxième", a["title"]]  # plus récent en premier
    assert pending[2]["telegram_message_id"] == 42

    done = await store.apply_alert_action(a["id"], "executed", actor="ui")
    assert done["status"] == "executed" and done["acted_at"] and done["acted_by"] == "ui"
    with pytest.raises(AlertAlreadyHandled) as exc:
        await store.apply_alert_action(a["id"], "ignored", actor="telegram")
    assert exc.value.alert["status"] == "executed"

    ignored = await store.apply_alert_action(b["id"], "ignored", actor="telegram")
    assert ignored["status"] == "ignored" and ignored["acted_by"] == "telegram"

    postponed = await store.apply_alert_action(c["id"], "postponed", postpone_hours=6)
    assert postponed["status"] == "postponed"
    until = datetime.fromisoformat(postponed["postponed_until"])
    assert timedelta(hours=5, minutes=59) < until - datetime.now(UTC) <= timedelta(hours=6)

    with pytest.raises(AlertNotFound):
        await store.apply_alert_action("missing", "executed")
    with pytest.raises(ValueError):
        await store.apply_alert_action(c["id"], "snooze")

    assert len(await store.list_alerts("pending")) == 0
    assert len(await store.list_alerts("all")) == 3
    assert len(await store.list_alerts("postponed")) == 1
    assert len(await store.list_alerts("all", limit=2)) == 2
    with pytest.raises(ValueError):
        await store.list_alerts("weird")


async def test_postponed_alert_wakes_up_when_expired(store):
    a = await _alert(store)
    await store.apply_alert_action(a["id"], "postponed", postpone_hours=24)
    assert await store.list_alerts("pending") == []
    # on force l'échéance dans le passé
    async with store._sf() as session:
        row = await session.get(WatchAlert, a["id"])
        row.postponed_until = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    woken = await store.list_alerts("pending")
    assert len(woken) == 1 and woken[0]["status"] == "pending" and woken[0]["postponed_until"] is None
    # une fois réveillée, elle peut être traitée
    assert (await store.apply_alert_action(a["id"], "executed"))["status"] == "executed"


async def test_module_level_apply_alert_action(store):
    with pytest.raises(RuntimeError):
        await store_mod.apply_alert_action("x", "executed")
    store_mod.configure(store._sf)
    a = await _alert(store)
    done = await store_mod.apply_alert_action(a["id"], "ignored", actor="telegram")
    assert done["status"] == "ignored" and done["acted_by"] == "telegram"
    assert store_mod.get_store() is not None


# ------------------------------------------------------------------ snapshots

async def test_snapshots_history_and_hourly_cap(store):
    now = datetime.now(UTC)
    snap = {"total_usd": 1000.0, "categories": [{"category": "cash", "value_usd": 1000.0}], "positions": {}}
    assert await store.add_snapshot(dict(snap, total_usd=900), at=now - timedelta(days=40)) is not None
    assert await store.add_snapshot(dict(snap, total_usd=950), at=now - timedelta(days=20)) is not None
    assert await store.add_snapshot(dict(snap, total_usd=1000), at=now - timedelta(hours=2)) is not None
    assert await store.add_snapshot(dict(snap, total_usd=1001), at=now - timedelta(hours=1, minutes=30)) is None  # < 1 h après le précédent
    assert await store.add_snapshot(dict(snap, total_usd=1002), at=now) is not None

    h365 = await store.history(365)
    assert [p["value_usd"] for p in h365] == [900.0, 950.0, 1000.0, 1002.0]
    assert h365[0]["at"] < h365[-1]["at"]
    assert [p["value_usd"] for p in await store.history(30)] == [950.0, 1000.0, 1002.0]
    assert [p["value_usd"] for p in await store.history(7)] == [1000.0, 1002.0]
    latest = await store.latest_snapshot()
    assert latest["total_usd"] == 1002.0 and latest["categories"][0]["category"] == "cash"


# ------------------------------------------------------------------ Fear & Greed

async def test_fng_upsert_and_history(store):
    n = await store.upsert_daily([
        {"date": "2026-09-05", "value": 30, "classification": "Fear"},
        {"date": "2026-09-06", "value": "22", "classification": "Extreme Fear"},
        {"date": "bad", "value": 10, "classification": "x"},
        {"date": "2026-09-04", "value": "n/a", "classification": "x"},
    ])
    assert n == 2
    await store.upsert_daily([{"date": "2026-09-06", "value": 20, "classification": "Extreme Fear"}])
    hist = await store.fng_history(30)
    assert hist == [{"date": "2026-09-06", "value": 20, "classification": "Extreme Fear"},
                    {"date": "2026-09-05", "value": 30, "classification": "Fear"}]
    assert len(await store.fng_history(1)) == 1


# ------------------------------------------------------------------ runtime.evaluate_rules (câblage store + moteur)

async def test_runtime_evaluate_rules_persists_state_and_creates_alert_once(store):
    from watch import runtime

    rule = await store.create_rule({"family": "take_profit", "name": "AAPL", "symbol": "AAPL",
                                    "params": {"levels": [{"price": 250, "sell_pct": 25}], "pru": 190}})
    await store.create_rule({"family": "sentiment_zone", "name": "off", "enabled": False,
                             "params": {"zone": "extreme_fear", "consecutive_days": 1}})
    ctx = {"prices": {"AAPL": 260.0}, "positions": {"AAPL": {"units": 4, "pru": 190.0, "value_usd": 1040}},
           "allocation": {}, "total_usd": 1040.0, "fng_history": [{"date": "2026-09-06", "value": 5}], "now": None}
    created = await runtime.evaluate_rules(store, ctx)
    assert len(created) == 1 and created[0]["rule_id"] == rule["id"] and created[0]["status"] == "pending"
    assert (await store.get_rule(rule["id"]))["state"]["levels_fired"] == [True]
    assert await runtime.evaluate_rules(store, ctx) == []                  # pas de doublon (état persisté)
    assert len(await store.list_alerts("pending")) == 1
