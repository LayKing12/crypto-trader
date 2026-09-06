"""Tests — notifications/api.py (webhook Telegram restreint aux chats autorisés)."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notifications import api as tg_api
from notifications import telegram_service
from notifications.config import TelegramSettings

API = "https://api.telegram.org"


@pytest.fixture
def client(monkeypatch):
    settings = TelegramSettings(TELEGRAM_ENABLED="true", TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_CHAT_ID_WATCH="111",
                                TELEGRAM_CHAT_ID_TRADING="222", TELEGRAM_WEBHOOK_SECRET="s3cret", TELEGRAM_API_BASE=API)
    monkeypatch.setattr(tg_api, "get_telegram_settings", lambda: settings)
    svc = telegram_service.TelegramService(settings, httpx.AsyncClient())
    monkeypatch.setattr(tg_api, "get_service", lambda: svc)
    app = FastAPI()
    app.include_router(tg_api.router)
    return TestClient(app)


def _callback(chat_id="111", data="alert:a1:executed"):
    return {"callback_query": {"id": "cq1", "from": {"id": int(chat_id)}, "data": data,
                               "message": {"message_id": 5, "chat": {"id": int(chat_id)}}}}


def test_bad_secret_401_and_missing_503(client, monkeypatch):
    assert client.post("/telegram/webhook", json=_callback(), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 401
    assert client.post("/telegram/webhook", json=_callback()).status_code == 401
    monkeypatch.setattr(tg_api, "get_telegram_settings", lambda: TelegramSettings(TELEGRAM_ENABLED="true", TELEGRAM_BOT_TOKEN="x"))
    assert client.post("/telegram/webhook", json=_callback(), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"}).status_code == 503


@respx.mock
def test_unauthorized_chat_is_ignored(client):
    api = respx.post(url__regex=rf"{API}/.*").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    r = client.post("/telegram/webhook", json=_callback(chat_id="999"), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["ignored"] is True
    assert not api.called


@respx.mock
def test_valid_callback_applies_action_and_removes_buttons(client, monkeypatch):
    calls: list[tuple] = []

    async def fake_apply(alert_id, action, actor="?"):
        calls.append((alert_id, action, actor))
        return {"id": alert_id, "status": action}

    import types, sys
    store = types.ModuleType("watch.rules.store")
    store.apply_alert_action = fake_apply
    monkeypatch.setitem(sys.modules, "watch.rules.store", store)

    answer = respx.post(f"{API}/bot123:ABC/answerCallbackQuery").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    edit = respx.post(f"{API}/bot123:ABC/editMessageReplyMarkup").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    r = client.post("/telegram/webhook", json=_callback(), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["applied"] is True
    assert calls == [("a1", "executed", "telegram")]
    assert answer.called and edit.called


@respx.mock
def test_unknown_callback_data(client):
    answer = respx.post(f"{API}/bot123:ABC/answerCallbackQuery").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    r = client.post("/telegram/webhook", json=_callback(data="foo"), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["applied"] is False and answer.called
