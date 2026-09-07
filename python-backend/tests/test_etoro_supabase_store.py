"""Tests de SupabaseStore : PostgREST mocké avec respx, aucune exception ne remonte à l'appelant."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from etoro.config import Settings
from etoro.supabase_store import DECISIONS_TABLE, STATE_TABLE, SupabaseStore

URL = "https://x.supabase.co"
KEY = "service-key"
STATE_URL = f"{URL}/rest/v1/{STATE_TABLE}"
DECISIONS_URL = f"{URL}/rest/v1/{DECISIONS_TABLE}"


@pytest.fixture
def store():
    with httpx.Client() as client:
        yield SupabaseStore(URL + "/", KEY, client=client)  # slash final toléré


# ------------------------------------------------------------------ get_state
@respx.mock
def test_get_state_returns_data_when_row_present(store):
    route = respx.get(STATE_URL).mock(return_value=httpx.Response(200, json=[{"data": {"kill_switch": True}}]))
    assert store.get_state() == {"kill_switch": True}
    req = route.calls.last.request
    assert req.url.params["id"] == "eq.main"
    assert req.url.params["select"] == "data"
    assert req.headers["apikey"] == KEY
    assert req.headers["Authorization"] == f"Bearer {KEY}"


@respx.mock
def test_get_state_returns_none_when_row_absent(store):
    respx.get(STATE_URL).mock(return_value=httpx.Response(200, json=[]))
    assert store.get_state() is None


@respx.mock
def test_get_state_returns_none_when_data_is_not_a_dict(store):
    respx.get(STATE_URL).mock(return_value=httpx.Response(200, json=[{"data": "oops"}]))
    assert store.get_state() is None


@respx.mock
def test_get_state_returns_none_on_server_error(store, caplog):
    respx.get(STATE_URL).mock(return_value=httpx.Response(500, text="boom"))
    with caplog.at_level("WARNING", logger="etoro.supabase_store"):
        assert store.get_state() is None
    assert any("get_state" in m for m in caplog.messages)


@respx.mock
def test_get_state_returns_none_on_network_error(store):
    respx.get(STATE_URL).mock(side_effect=httpx.ConnectError("réseau coupé"))
    assert store.get_state() is None


# ---------------------------------------------------------------- upsert_state
@respx.mock
def test_upsert_state_posts_merge_duplicates_with_main_row(store):
    route = respx.post(STATE_URL).mock(return_value=httpx.Response(201))
    data = {"daily_pnl": -1.5, "kill_switch": False}
    assert store.upsert_state(data) is True
    req = route.calls.last.request
    assert "merge-duplicates" in req.headers["Prefer"]
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(req.content) == {"id": "main", "data": data}


@respx.mock
def test_upsert_state_returns_false_on_error(store):
    respx.post(STATE_URL).mock(return_value=httpx.Response(500))
    assert store.upsert_state({"a": 1}) is False


# ------------------------------------------------------------- insert_decision
@respx.mock
def test_insert_decision_posts_columns_and_full_record(store):
    route = respx.post(DECISIONS_URL).mock(return_value=httpx.Response(201))
    rec = {"id": "d1", "at": "2026-09-06T10:00:00+00:00", "kind": "open", "symbol": "AAPL", "reason": "opened"}
    assert store.insert_decision(rec) is True
    req = route.calls.last.request
    assert "ignore-duplicates" in req.headers["Prefer"]
    assert json.loads(req.content) == {
        "id": "d1", "at": "2026-09-06T10:00:00+00:00", "kind": "open", "symbol": "AAPL", "data": rec,
    }


@respx.mock
def test_insert_decision_returns_false_on_error(store):
    respx.post(DECISIONS_URL).mock(side_effect=httpx.ReadTimeout("lent"))
    assert store.insert_decision({"id": "d1"}) is False


# ------------------------------------------------------------ recent_decisions
@respx.mock
def test_recent_decisions_orders_by_at_desc_and_unwraps_data(store):
    route = respx.get(DECISIONS_URL).mock(
        return_value=httpx.Response(200, json=[{"data": {"id": "new"}}, {"data": "bad"}, {"data": {"id": "old"}}])
    )
    assert store.recent_decisions(limit=42) == [{"id": "new"}, {"id": "old"}]
    params = route.calls.last.request.url.params
    assert params["order"] == "at.desc"
    assert params["limit"] == "42"
    assert params["select"] == "data"


@respx.mock
def test_recent_decisions_returns_empty_list_on_error(store):
    respx.get(DECISIONS_URL).mock(return_value=httpx.Response(503))
    assert store.recent_decisions() == []


# --------------------------------------------------------------------- health
@respx.mock
def test_health_true_on_200_false_otherwise(store):
    route = respx.get(STATE_URL).mock(return_value=httpx.Response(200, json=[]))
    assert store.health() is True
    assert route.calls.last.request.url.params["limit"] == "1"
    route.mock(return_value=httpx.Response(401))
    assert store.health() is False
    route.mock(side_effect=httpx.ConnectError("hors ligne"))
    assert store.health() is False


# -------------------------------------------------------------- from_settings
def test_from_settings_returns_none_when_not_configured():
    settings = Settings(ETORO_API_KEY="test", SUPABASE_URL=None, SUPABASE_SERVICE_KEY=None, _env_file=None)
    assert SupabaseStore.from_settings(settings) is None
    only_url = Settings(ETORO_API_KEY="test", SUPABASE_URL=URL, SUPABASE_SERVICE_KEY=None, _env_file=None)
    assert SupabaseStore.from_settings(only_url) is None


def test_from_settings_builds_store_when_configured():
    settings = Settings(ETORO_API_KEY="test", SUPABASE_URL=URL, SUPABASE_SERVICE_KEY="k", _env_file=None)
    store = SupabaseStore.from_settings(settings)
    try:
        assert isinstance(store, SupabaseStore)
        assert store._base == f"{URL}/rest/v1"
        assert store._headers["apikey"] == "k"
        assert store._owns_client is True
    finally:
        store.close()


def test_close_only_closes_owned_client():
    client = httpx.Client()
    try:
        SupabaseStore(URL, KEY, client=client).close()
        assert client.is_closed is False
    finally:
        client.close()
    owned = SupabaseStore(URL, KEY)
    owned.close()
    assert owned._client.is_closed is True
