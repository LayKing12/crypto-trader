"""Tests de persistance de StateStore (JSON atomique)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest

from etoro.state_store import StateStore

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def test_load_missing_file_starts_empty(tmp_path):
    store = StateStore(str(tmp_path / "missing" / "state.json"))
    store.load()
    assert store.open_positions_by_instrument == {}
    assert store.last_trade_at == {}
    assert store.daily_pnl == 0.0
    assert store.daily_pnl_date is None
    assert store.equity_start_of_day == 0.0
    assert store.breaker_until is None
    assert store.kill_switch is False
    assert store.kill_switch_actor is None


def test_save_creates_parent_dir_and_no_tmp_left(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    store = StateStore(str(path))
    store.save()
    assert path.exists()
    assert not path.with_name("state.json.tmp").exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kill_switch"] is False


def test_roundtrip_persistence(tmp_path):
    path = tmp_path / "s.json"
    store = StateStore(str(path))
    store.record_open(1001, "pos-1", NOW)
    store.record_open(2002, "pos-2", NOW + timedelta(minutes=5))
    store.daily_pnl = -12.5
    store.daily_pnl_date = date(2026, 9, 5)
    store.equity_start_of_day = 10_000.0
    store.breaker_until = NOW + timedelta(hours=24)
    store.set_kill_switch(True, "alice")

    reloaded = StateStore(str(path))
    reloaded.load()
    assert reloaded.open_positions_by_instrument == {1001: "pos-1", 2002: "pos-2"}
    assert all(isinstance(k, int) for k in reloaded.open_positions_by_instrument)
    assert reloaded.last_trade_at == {1001: NOW, 2002: NOW + timedelta(minutes=5)}
    assert all(isinstance(k, int) for k in reloaded.last_trade_at)
    assert reloaded.last_trade_at[1001].tzinfo is not None
    assert reloaded.daily_pnl == -12.5
    assert reloaded.daily_pnl_date == date(2026, 9, 5)
    assert reloaded.equity_start_of_day == 10_000.0
    assert reloaded.breaker_until == NOW + timedelta(hours=24)
    assert reloaded.breaker_until.tzinfo is not None
    assert reloaded.kill_switch is True
    assert reloaded.kill_switch_actor == "alice"


def test_datetimes_serialized_iso8601_with_string_keys(tmp_path):
    path = tmp_path / "s.json"
    store = StateStore(str(path))
    store.record_open(42, "p", NOW)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["last_trade_at"] == {"42": "2026-09-05T12:00:00+00:00"}
    assert raw["open_positions_by_instrument"] == {"42": "p"}


def test_corrupt_file_starts_empty_and_warns(tmp_path, caplog):
    path = tmp_path / "s.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = StateStore(str(path))
    with caplog.at_level(logging.WARNING, logger="etoro.state_store"):
        store.load()
    assert store.open_positions_by_instrument == {}
    assert store.kill_switch is False
    assert any("illisible" in rec.getMessage() for rec in caplog.records)


def test_corrupt_structure_starts_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"last_trade_at": {"1": "pas-une-date"}}), encoding="utf-8")
    store = StateStore(str(path))
    store.load()
    assert store.last_trade_at == {}


def test_load_json_list_root_is_corrupt(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = StateStore(str(path))
    store.load()
    assert store.snapshot()["open_positions_count"] == 0


def test_reset_day(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    store.daily_pnl = -250.0
    store.daily_pnl_date = date(2026, 9, 4)
    store.equity_start_of_day = 9_000.0
    store.breaker_until = NOW + timedelta(hours=3)
    store.reset_day(equity=10_500.0, today=date(2026, 9, 5))
    assert store.daily_pnl == 0.0
    assert store.daily_pnl_date == date(2026, 9, 5)
    assert store.equity_start_of_day == 10_500.0
    # Le breaker n'est pas leve par le changement de jour.
    assert store.breaker_until == NOW + timedelta(hours=3)
    reloaded = StateStore(store.path)
    reloaded.load()
    assert reloaded.daily_pnl_date == date(2026, 9, 5)


def test_record_open_and_close_update_cooldown(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    store.record_open(7, "pos-7", NOW)
    assert store.open_positions_by_instrument == {7: "pos-7"}
    assert store.last_trade_at[7] == NOW
    later = NOW + timedelta(hours=2)
    store.record_close(7, later)
    assert store.open_positions_by_instrument == {}
    assert store.last_trade_at[7] == later


def test_record_close_unknown_instrument_is_safe(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    store.record_close(99, NOW)
    assert store.open_positions_by_instrument == {}
    assert store.last_trade_at[99] == NOW


def test_naive_datetime_is_treated_as_utc(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    store.record_open(1, "p", datetime(2026, 9, 5, 12, 0))
    assert store.last_trade_at[1] == NOW


def test_set_kill_switch_persists(tmp_path):
    path = tmp_path / "s.json"
    store = StateStore(str(path))
    store.set_kill_switch(True, "ops")
    reloaded = StateStore(str(path))
    reloaded.load()
    assert reloaded.kill_switch is True and reloaded.kill_switch_actor == "ops"
    store.set_kill_switch(False, "ops")
    reloaded.load()
    assert reloaded.kill_switch is False


def test_snapshot_is_json_serializable(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    store.record_open(1, "p", NOW)
    store.breaker_until = NOW
    store.daily_pnl_date = date(2026, 9, 5)
    snap = store.snapshot()
    json.dumps(snap)
    assert snap["open_positions_count"] == 1
    assert snap["open_positions_by_instrument"] == {"1": "p"}
    assert snap["breaker_until"] == NOW.isoformat()
    assert snap["daily_pnl_date"] == "2026-09-05"
    assert "saved_at" not in snap


@pytest.mark.parametrize("value", [None, ""])
def test_empty_date_fields_load_as_none(tmp_path, value):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"breaker_until": value, "daily_pnl_date": value}), encoding="utf-8")
    store = StateStore(str(path))
    store.load()
    assert store.breaker_until is None and store.daily_pnl_date is None
