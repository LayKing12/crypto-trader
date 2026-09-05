"""Tests de DecisionLog : JSONL append-only, ring buffer mémoire, rechargement tolérant."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from etoro.decision_log import DecisionLog, new_record
from etoro.models import DecisionRecord, Side


def _rec(kind: str = "signal", symbol: str | None = "AAPL", reason: str = "received", **kw) -> DecisionRecord:
    return new_record(kind, reason, "Analyse", symbol=symbol, **kw)


def test_new_record_generates_id_and_utc_timestamp():
    a, b = _rec(), _rec()
    assert a.id != b.id and len(a.id) == 36
    assert a.at.tzinfo is not None and a.at.utcoffset().total_seconds() == 0
    assert a.kind == "signal" and a.reason == "received" and a.action == "Analyse" and a.mode == "demo"


def test_record_appends_jsonl_and_recent_is_newest_first(tmp_path):
    path = tmp_path / "sub" / "dir" / "decisions.jsonl"  # dossiers parents absents : créés
    log = DecisionLog(str(path))
    first = log.record(_rec("signal"))
    second = log.record(_rec("open", reason="opened", side=Side.BUY, amount=100.0, entry_rate=190.12))
    third = log.record(_rec("close", reason="closed", realized_pnl=12.3))

    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["id"] for p in parsed] == [first.id, second.id, third.id]
    assert parsed[1]["side"] == "BUY" and parsed[1]["amount"] == 100.0
    assert isinstance(parsed[0]["at"], str)  # sérialisé en JSON (mode="json")

    recent = log.recent()
    assert [r.id for r in recent] == [third.id, second.id, first.id]
    assert [r.id for r in log.recent(limit=2)] == [third.id, second.id]
    assert log.recent(limit=0) == []
    assert len(log) == 3


def test_recent_filters_by_kind_and_symbol():
    log = DecisionLog(None)
    log.record(_rec("signal", "AAPL"))
    log.record(_rec("skip", "MSFT", reason="cooldown_active"))
    log.record(_rec("open", "AAPL", reason="opened"))
    log.record(_rec("kill_switch", None, reason="kill_switch"))

    assert [r.kind for r in log.recent(kind="open")] == ["open"]
    assert [(r.kind, r.symbol) for r in log.recent(symbol="aapl")] == [("open", "AAPL"), ("signal", "AAPL")]
    assert [r.reason for r in log.recent(kind="skip", symbol="MSFT")] == ["cooldown_active"]
    assert log.recent(kind="skip", symbol="AAPL") == []
    assert log.recent(kind="unknown") == []


def test_load_tail_skips_corrupted_lines(tmp_path, caplog):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLog(str(path))
    kept = [writer.record(_rec("signal")), writer.record(_rec("open", reason="opened"))]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
        fh.write("\n")  # ligne vide : ignorée sans warning
        fh.write(json.dumps({"id": "x", "kind": "martian"}) + "\n")  # JSON valide mais record invalide
    last = writer.record(_rec("close", reason="closed", realized_pnl=-1.0))

    reader = DecisionLog(str(path))
    assert reader.recent() == []
    with caplog.at_level(logging.WARNING, logger="etoro.decision_log"):
        reader.load_tail()
    assert [r.id for r in reader.recent()] == [last.id, kept[1].id, kept[0].id]
    assert reader.recent()[0].realized_pnl == -1.0
    assert any("2 ligne(s) corrompue(s)" in m for m in caplog.messages)


def test_load_tail_keeps_only_last_max_memory_valid_lines(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLog(str(path))
    ids = [writer.record(_rec("signal", reason=f"r{i}")).id for i in range(5)]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("corrupted\n")

    reader = DecisionLog(str(path), max_memory=2)
    reader.load_tail()
    assert [r.id for r in reader.recent()] == [ids[4], ids[3]]


def test_load_tail_without_file_is_noop(tmp_path):
    log = DecisionLog(str(tmp_path / "missing.jsonl"))
    log.load_tail()
    assert log.recent() == []


def test_memory_only_mode_when_path_is_none_or_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for path in (None, ""):
        log = DecisionLog(path)
        assert log.memory_only is True
        rec = log.record(_rec())
        log.load_tail()  # no-op
        assert [r.id for r in log.recent()] == [rec.id]
    assert list(tmp_path.iterdir()) == []  # aucun fichier créé


def test_ring_buffer_limit_keeps_newest(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(str(path), max_memory=3)
    ids = [log.record(_rec("signal", reason=f"r{i}")).id for i in range(5)]
    assert [r.id for r in log.recent()] == [ids[4], ids[3], ids[2]]
    assert len(log) == 3
    # le fichier, lui, conserve tout (append-only)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_record_never_raises_when_file_unwritable(tmp_path, caplog):
    log = DecisionLog(str(tmp_path))  # un dossier : l'ouverture en écriture échoue
    with caplog.at_level(logging.ERROR, logger="etoro.decision_log"):
        rec = log.record(_rec())
    assert [r.id for r in log.recent()] == [rec.id]  # conservé en mémoire malgré l'échec disque
    assert any("écriture impossible" in m for m in caplog.messages)


def test_record_roundtrip_preserves_all_fields(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(str(path))
    rec = DecisionRecord(
        id="abc",
        at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        kind="open",
        symbol="AAPL",
        instrument_id=1,
        side=Side.BUY,
        market_score=85.0,
        rankings_confirmation=0.7,
        sentiment=0.2,
        reason="opened",
        action="Ouverture BUY AAPL 100.00 USD @ 190.1200 SL 186.3200 TP 197.7200",
        position_id="pos-1",
        amount=100.0,
        entry_rate=190.12,
        stop_loss_rate=186.32,
        take_profit_rate=197.72,
        mode="real",
    )
    log.record(rec)
    reader = DecisionLog(str(path))
    reader.load_tail()
    assert reader.recent() == [rec]
