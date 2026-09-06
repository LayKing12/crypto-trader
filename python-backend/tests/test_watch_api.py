"""Tests — watch/api.py (lecture seule : observations et statut)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from watch import api as watch_api
from watch.config import WatchSettings
from watch.observation_log import ObservationLog, new_observation


def _client():
    log = ObservationLog(None)
    log.record(new_observation("price_move", "kraken", "BTC +3 %", symbol="BTCUSD", detail={"change_pct": 3.0}))
    log.record(new_observation("news", "yahoo", "Apple beats", symbol="AAPL", detail={"url": "https://x"}))
    watch_api.configure(log, settings=WatchSettings(WATCH_ENABLED="true", WATCH_LOG_PATH=""))
    app = FastAPI()
    app.include_router(watch_api.router)
    return TestClient(app)


def test_observations_with_filters():
    c = _client()
    body = c.get("/watch/observations").json()
    assert body["available"] is True and body["count"] == 2
    assert body["observations"][0]["kind"] == "news"  # plus récent en premier
    assert c.get("/watch/observations?kind=price_move").json()["count"] == 1
    assert c.get("/watch/observations?symbol=AAPL").json()["count"] == 1
    assert c.get("/watch/observations?source=kraken").json()["count"] == 1
    assert c.get("/watch/observations?limit=1").json()["count"] == 1


def test_status_and_unconfigured():
    c = _client()
    st = c.get("/watch/status").json()
    assert st["enabled"] is True and st["observations"] == 2
    watch_api.reset()
    app = FastAPI()
    app.include_router(watch_api.router)
    body = TestClient(app).get("/watch/observations").json()
    assert body["available"] is False and body["observations"] == []
