"""Tests du router FastAPI eToro avec des dépendances fake (aucun import de state_store / risk_guard)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from etoro import api
from etoro.config import Settings
from etoro.decision_log import DecisionLog, new_record


class FakeStore:
    def __init__(self):
        self.kill_switch = False
        self.kill_switch_actor = None
        self.breaker_until = None
        self.saved = 0

    def set_kill_switch(self, enabled: bool, actor: str) -> None:
        self.kill_switch = enabled
        self.kill_switch_actor = actor
        self.saved += 1

    def snapshot(self) -> dict:
        return {"kill_switch": self.kill_switch, "daily_pnl": 0.0, "open_positions": {}}


class FakeGuard:
    def __init__(self, store: FakeStore, settings: Settings):
        self.store = store
        self.settings = settings

    def is_breaker_active(self) -> bool:
        return bool(self.store.breaker_until and self.store.breaker_until > datetime.now(timezone.utc))

    def kill_switch_active(self) -> bool:
        return self.store.kill_switch or not self.settings.etoro_agent_enabled


class FakeNotifier:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    async def send_kill_switch(self, actor: str, enabled: bool) -> None:
        self.calls.append((actor, enabled))


def _settings(**overrides) -> Settings:
    base = dict(ETORO_API_KEY="test", ETORO_KILL_SWITCH_TOKEN="secret")
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def ctx():
    store = FakeStore()
    settings = _settings()
    notifier = FakeNotifier()
    api.configure(store=store, guard=FakeGuard(store, settings), notifier=notifier, settings=settings)
    client = TestClient(api.create_app(autowire=False))
    yield client, store, notifier
    api.configure(store=None)


def test_health(ctx):
    client, _, _ = ctx
    r = client.get("/etoro/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["mode"] == "demo"


def test_kill_mauvais_token(ctx):
    client, store, notifier = ctx
    assert client.post("/etoro/kill", headers={"X-Kill-Token": "wrong"}).status_code == 401
    assert client.post("/etoro/kill").status_code == 401
    assert store.kill_switch is False and notifier.calls == []


def test_kill_sans_token_configure():
    store = FakeStore()
    api.configure(store=store, settings=_settings(ETORO_KILL_SWITCH_TOKEN=None))
    client = TestClient(api.create_app(autowire=False))
    assert client.post("/etoro/kill", headers={"X-Kill-Token": "secret"}).status_code == 503
    assert store.kill_switch is False
    api.configure(store=None)


def test_kill_puis_status_puis_resume(ctx):
    client, store, notifier = ctx
    assert client.get("/etoro/status").json()["kill_switch"] is False

    r = client.post("/etoro/kill", headers={"X-Kill-Token": "secret", "X-Kill-Actor": "aliou"})
    assert r.status_code == 200
    assert r.json() == {"kill_switch": True, "actor": "aliou", "mode": "demo"}
    assert store.kill_switch is True and store.kill_switch_actor == "aliou"
    assert notifier.calls == [("aliou", True)]

    status = client.get("/etoro/status").json()
    assert status["kill_switch"] is True
    assert status["kill_switch_active"] is True
    assert status["breaker_active"] is False and status["breaker_until"] is None
    assert status["snapshot"]["kill_switch"] is True
    assert status["mode"] == "demo"

    r = client.post("/etoro/resume", headers={"X-Kill-Token": "secret"})
    assert r.status_code == 200 and r.json()["kill_switch"] is False
    assert store.kill_switch is False
    assert notifier.calls[-1] == ("api", False)
    assert client.get("/etoro/status").json()["kill_switch"] is False


def test_status_breaker(ctx):
    client, store, _ = ctx
    store.breaker_until = datetime.now(timezone.utc) + timedelta(hours=2)
    status = client.get("/etoro/status").json()
    assert status["breaker_active"] is True
    assert status["breaker_until"] == store.breaker_until.isoformat()


def test_status_sans_store():
    api.configure(store=None)
    client = TestClient(api.create_app(autowire=False))
    assert client.get("/etoro/status").status_code == 503
    assert client.get("/etoro/health").status_code == 200


def test_include_router_dans_app_existante(ctx):
    _, store, _ = ctx
    host = FastAPI()
    host.include_router(api.router)
    client = TestClient(host)
    assert client.get("/etoro/status").json()["kill_switch"] is store.kill_switch


# ---------------------------------------------------------------------- /etoro/decisions
def test_decisions_sans_journal_injecte(ctx):
    client, _, _ = ctx
    r = client.get("/etoro/decisions")
    assert r.status_code == 200
    assert r.json() == {"decisions": [], "count": 0, "available": False}


def _journal_client(records) -> tuple[TestClient, DecisionLog]:
    log = DecisionLog(None, max_memory=1000)
    for rec in records:
        log.record(rec)
    api.configure(store=FakeStore(), settings=_settings(), decision_log=log)
    return TestClient(api.create_app(autowire=False)), log


def test_decisions_avec_journal_injecte():
    client, log = _journal_client(
        [
            new_record("signal", "received", "Analyse", symbol="AAPL", instrument_id=1, market_score=80.0),
            new_record("skip", "cooldown_active", "Aucune action", symbol="MSFT", instrument_id=2),
            new_record(
                "open",
                "opened",
                "Ouverture BUY AAPL 1000.00 USD @ 200.0000 SL 196.0000 TP 208.0000",
                symbol="AAPL",
                instrument_id=1,
                position_id="pos-1",
                amount=1000.0,
                entry_rate=200.0,
            ),
        ]
    )
    try:
        body = client.get("/etoro/decisions").json()
        assert body["available"] is True and body["count"] == 3
        assert [d["kind"] for d in body["decisions"]] == ["open", "skip", "signal"]  # plus récent en premier
        newest = body["decisions"][0]
        assert newest["position_id"] == "pos-1" and newest["amount"] == 1000.0 and newest["mode"] == "demo"
        assert isinstance(newest["at"], str) and isinstance(newest["id"], str)

        by_symbol = client.get("/etoro/decisions?symbol=aapl").json()
        assert [d["symbol"] for d in by_symbol["decisions"]] == ["AAPL", "AAPL"] and by_symbol["count"] == 2
        by_kind = client.get("/etoro/decisions?kind=skip").json()
        assert by_kind["count"] == 1 and by_kind["decisions"][0]["reason"] == "cooldown_active"
        assert client.get("/etoro/decisions?kind=skip&symbol=AAPL").json()["count"] == 0
        assert client.get("/etoro/decisions?kind=&symbol=").json()["count"] == 3  # filtres vides ignorés

        assert client.get("/etoro/decisions?limit=1").json()["count"] == 1
        assert client.get("/etoro/decisions?limit=0").json()["count"] == 1  # borné à 1
        assert client.get("/etoro/decisions?limit=-5").json()["count"] == 1
        assert client.get("/etoro/decisions?limit=abc").status_code == 422
    finally:
        api.configure(store=None)


def test_decisions_limit_borne_a_500():
    client, _ = _journal_client(
        [new_record("signal", "received", "Analyse", symbol="AAPL") for _ in range(600)]
    )
    try:
        assert client.get("/etoro/decisions?limit=9999").json()["count"] == 500
        assert client.get("/etoro/decisions").json()["count"] == 100  # défaut
    finally:
        api.configure(store=None)


def test_decisions_journal_en_erreur_repond_indisponible():
    class BrokenLog:
        def recent(self, **kw):
            raise RuntimeError("boom")

    api.configure(store=FakeStore(), settings=_settings(), decision_log=BrokenLog())
    try:
        client = TestClient(api.create_app(autowire=False))
        assert client.get("/etoro/decisions").json() == {"decisions": [], "count": 0, "available": False}
    finally:
        api.configure(store=None)


def test_health_exposes_agent_and_supabase_fields():
    from etoro import api as api_mod
    from etoro.config import Settings

    class _Store:
        kill_switch = False
        kill_switch_actor = None
        breaker_until = None

        def snapshot(self):
            return {}

    api_mod.configure(store=_Store(), settings=Settings(ETORO_API_KEY="test"))
    api_mod._deps.remote = None
    api_mod._deps.agent_task = None
    from fastapi.testclient import TestClient

    with TestClient(api_mod.create_app(autowire=False)) as client:
        body = client.get("/etoro/health").json()
    assert body["agent_running"] is False
    assert body["supabase"] == "off"
    assert body["run_agent"] is False


def test_rankings_debug_endpoint():
    from etoro import api as api_mod
    from etoro.config import Settings

    class _Store:
        kill_switch = False
        kill_switch_actor = None
        breaker_until = None

        def snapshot(self):
            return {}

    api_mod.configure(store=_Store(), settings=Settings(ETORO_API_KEY="test"))
    from fastapi.testclient import TestClient

    with TestClient(api_mod.create_app(autowire=False)) as client:
        body = client.get("/etoro/rankings/debug").json()
    assert body["available"] is True and "instruments" in body and body["min_confirmation"] == 0.3
