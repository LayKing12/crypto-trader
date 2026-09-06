"""Tests — watch/observation_log.py (journal JSONL distinct de decision_log)."""
from __future__ import annotations

from watch.observation_log import Observation, ObservationLog, new_observation


def _obs(kind="price_move", source="kraken", symbol="BTCUSD", title="t"):
    return new_observation(kind, source, title, symbol=symbol, detail={"x": 1})


def test_record_and_recent_order(tmp_path):
    log = ObservationLog(str(tmp_path / "obs.jsonl"))
    a = log.record(_obs(title="a"))
    b = log.record(_obs(title="b"))
    got = log.recent(limit=10)
    assert [o.id for o in got] == [b.id, a.id]
    assert (tmp_path / "obs.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_filters(tmp_path):
    log = ObservationLog(str(tmp_path / "obs.jsonl"))
    log.record(_obs(kind="news", source="yahoo", symbol="AAPL"))
    log.record(_obs(kind="price_move", source="etoro", symbol="AAPL"))
    log.record(_obs(kind="price_move", source="kraken", symbol="BTCUSD"))
    assert len(log.recent(kind="news")) == 1
    assert len(log.recent(symbol="AAPL")) == 2
    assert len(log.recent(source="kraken")) == 1
    assert len(log.recent(limit=1)) == 1


def test_load_tail_ignores_corrupted_lines(tmp_path):
    path = tmp_path / "obs.jsonl"
    log = ObservationLog(str(path))
    log.record(_obs(title="ok"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    fresh = ObservationLog(str(path))
    fresh.load_tail()
    assert [o.title for o in fresh.recent()] == ["ok"]


def test_memory_only_and_ring_buffer():
    log = ObservationLog(None, max_memory=3)
    assert log.memory_only
    for i in range(5):
        log.record(_obs(title=str(i)))
    assert [o.title for o in log.recent()] == ["4", "3", "2"]


def test_observation_model_is_json_serializable():
    obs = _obs()
    data = obs.model_dump(mode="json")
    assert data["kind"] == "price_move" and isinstance(data["at"], str)
    assert Observation.model_validate(data).id == obs.id
