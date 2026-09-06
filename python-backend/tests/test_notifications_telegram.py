"""Tests — notifications/telegram_service.py (Bot API mockée par respx)."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from notifications.config import TelegramSettings
from notifications.telegram_service import ALERT_ACTIONS, TelegramService

API = "https://api.telegram.org"


def _settings(**kw) -> TelegramSettings:
    base = dict(TELEGRAM_ENABLED="true", TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_CHAT_ID_WATCH="111",
                TELEGRAM_CHAT_ID_TRADING="222", TELEGRAM_WEBHOOK_SECRET="s3cret", TELEGRAM_API_BASE=API)
    base.update(kw)
    return TelegramSettings(**base)


def _ok(message_id=42):
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


@pytest.mark.asyncio
@respx.mock
async def test_send_text_builds_request():
    route = respx.post(f"{API}/bot123:ABC/sendMessage").mock(return_value=_ok(7))
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(), client)
        mid = await svc.send_text("trading", "Ouverture BUY AAPL")
    assert mid == 7
    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == "222" and body["text"] == "Ouverture BUY AAPL" and "reply_markup" not in body


@pytest.mark.asyncio
@respx.mock
async def test_noop_when_not_configured():
    route = respx.post(url__regex=rf"{API}/.*").mock(return_value=_ok())
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(TELEGRAM_ENABLED="false"), client)
        assert await svc.send_text("watch", "x") is None
        assert await svc.send_watch_alert({"id": "a1", "title": "t"}) is None
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_watch_alert_has_three_buttons():
    route = respx.post(f"{API}/bot123:ABC/sendMessage").mock(return_value=_ok(9))
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(), client)
        mid = await svc.send_watch_alert({"id": "a1", "title": "AAPL palier 1", "family": "take_profit",
                                          "rule_name": "AAPL TP", "symbol": "AAPL", "created_at": "2026-09-06T10:00:00Z"})
    assert mid == 9
    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == "111" and "AAPL palier 1" in body["text"]
    buttons = body["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [f"alert:a1:{a}" for _, a in ALERT_ACTIONS]


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_but_critical_passes():
    route = respx.post(f"{API}/bot123:ABC/sendMessage").mock(return_value=_ok())
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(TELEGRAM_MAX_PER_HOUR="2"), client)
        assert await svc.send_trading_event("a") is not None
        assert await svc.send_trading_event("b") is not None
        assert await svc.send_trading_event("c") is None            # limite atteinte
        assert await svc.send_trading_event("Circuit breaker déclenché") is not None  # critique
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_api_error_returns_none_without_raising():
    respx.post(f"{API}/bot123:ABC/sendMessage").mock(return_value=httpx.Response(400, json={"ok": False, "description": "bad"}))
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(), client)
        assert await svc.send_text("watch", "x") is None


@pytest.mark.asyncio
@respx.mock
async def test_setup_webhook_sends_secret():
    route = respx.post(f"{API}/bot123:ABC/setWebhook").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    async with httpx.AsyncClient() as client:
        svc = TelegramService(_settings(), client)
        assert await svc.setup_webhook("https://cryptomind.onrender.com/") is True
    body = json.loads(route.calls[0].request.content)
    assert body["url"] == "https://cryptomind.onrender.com/telegram/webhook" and body["secret_token"] == "s3cret"
