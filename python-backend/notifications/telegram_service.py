"""Telegram Bot API : alertes de veille (boutons inline) et événements de trading.

Deux chats séparés : « watch » (alertes à valider) et « trading » (ouvertures, fermetures, breaker,
kill switch). Ne lève jamais vers l'appelant ; no-op si non configuré. Anti-spam par chat
(TELEGRAM_MAX_PER_HOUR), sauf les messages critiques (breaker / kill switch) qui passent toujours.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

import httpx

from .config import TelegramSettings, get_telegram_settings

log = logging.getLogger(__name__)

ALERT_ACTIONS: tuple[tuple[str, str], ...] = (("✅ Exécuté", "executed"), ("🚫 Ignoré", "ignored"), ("⏰ Reporté", "postponed"))
_CRITICAL_WORDS = ("breaker", "kill", "coupé", "urgence")


class TelegramService:
    def __init__(self, settings: TelegramSettings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_telegram_settings()
        self._client = client
        self._owns_client = client is None
        self._sent: dict[str, deque[float]] = {}

    @property
    def settings(self) -> TelegramSettings:
        return self._settings

    @property
    def configured(self) -> bool:
        return self._settings.configured

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _url(self, method: str) -> str:
        return f"{self._settings.telegram_api_base.rstrip('/')}/bot{self._settings.telegram_bot_token}/{method}"

    def _rate_limited(self, chat_id: str) -> bool:
        now = time.monotonic()
        q = self._sent.setdefault(chat_id, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= self._settings.telegram_max_per_hour:
            return True
        q.append(now)
        return False

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.configured:
            log.debug("Telegram non configuré : %s ignoré", method)
            return None
        try:
            r = await self._http().post(self._url(method), json=payload)
            data = r.json() if r.content else {}
            if r.status_code != 200 or not data.get("ok", False):
                log.warning("Telegram %s : HTTP %s %s", method, r.status_code, str(data)[:200])
                return None
            return data.get("result") or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram %s : %s", method, exc)
            return None

    # ----------------------------------------------------------------- envoi
    async def send_text(self, chat: str, text: str, buttons: list[list[dict[str, str]]] | None = None,
                        *, priority: bool = False) -> int | None:
        """Envoie un message dans le chat « watch » ou « trading ». Retourne le message_id."""
        chat_id = self._settings.chat_id(chat)
        if not self.configured or not chat_id:
            return None
        if not priority and self._rate_limited(chat_id):
            log.warning("Telegram : anti-spam, message vers %s ignoré", chat)
            return None
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        result = await self._call("sendMessage", payload)
        return int(result["message_id"]) if result and "message_id" in result else None

    async def send_watch_alert(self, alert: dict[str, Any]) -> int | None:
        """Alerte de veille avec les boutons Exécuté / Ignoré / Reporté (callback_data alert:<id>:<action>)."""
        alert_id = str(alert.get("id", ""))
        lines = [f"👁️ {alert.get('title', 'Alerte de veille')}"]
        meta = " · ".join(str(x) for x in (alert.get("family"), alert.get("rule_name"), alert.get("symbol")) if x)
        if meta:
            lines.append(meta)
        if alert.get("created_at"):
            lines.append(f"🕒 {alert['created_at']}")
        buttons = [[{"text": label, "callback_data": f"alert:{alert_id}:{action}"} for label, action in ALERT_ACTIONS]]
        return await self.send_text("watch", "\n".join(lines), buttons)

    async def send_trading_event(self, text: str) -> int | None:
        priority = any(w in text.lower() for w in _CRITICAL_WORDS)
        return await self.send_text("trading", text, priority=priority)

    # ------------------------------------------------------------- callbacks
    async def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        return (await self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text[:200]})) is not None

    async def remove_buttons(self, chat_id: str | int, message_id: int) -> bool:
        return (await self._call("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []},
        })) is not None

    async def setup_webhook(self, public_url: str) -> bool:
        if not self._settings.telegram_webhook_secret:
            log.warning("TELEGRAM_WEBHOOK_SECRET manquant : webhook non enregistré")
            return False
        result = await self._call("setWebhook", {
            "url": public_url.rstrip("/") + "/telegram/webhook",
            "secret_token": self._settings.telegram_webhook_secret,
            "allowed_updates": ["callback_query", "message"],
            "drop_pending_updates": True,
        })
        return result is not None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


_default: TelegramService | None = None


def get_service() -> TelegramService:
    global _default
    if _default is None:
        _default = TelegramService()
    return _default


def reset_service() -> None:
    global _default
    _default = None


async def send_watch_alert(alert: dict[str, Any]) -> int | None:
    return await get_service().send_watch_alert(alert)


async def send_trading_event(text: str) -> int | None:
    return await get_service().send_trading_event(text)
