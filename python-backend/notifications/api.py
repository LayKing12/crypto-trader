"""Webhook Telegram : réception des clics sur les boutons d'alerte, restreint aux chats autorisés.

POST /telegram/webhook
  - header X-Telegram-Bot-Api-Secret-Token comparé en temps constant (401 sinon, 503 si non configuré)
  - tout update venant d'un chat / utilisateur hors TELEGRAM_CHAT_ID_* est ignoré (200 {"ignored": true})
  - callback_query "alert:<id>:<action>" -> watch.rules.store.apply_alert_action(...) (import tardif)
"""
from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .config import get_telegram_settings
from .telegram_service import get_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])
VALID_ACTIONS = {"executed", "ignored", "postponed"}


def _check_secret(given: str | None) -> None:
    expected = get_telegram_settings().telegram_webhook_secret
    if not expected:
        raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET non configuré")
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="secret invalide")


def _sender_ids(update: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    cq = update.get("callback_query") or {}
    msg = update.get("message") or cq.get("message") or {}
    for v in ((cq.get("from") or {}).get("id"), (msg.get("chat") or {}).get("id"), (msg.get("from") or {}).get("id")):
        if v is not None:
            ids.add(str(v))
    return ids


async def _apply_action(alert_id: str, action: str) -> tuple[bool, str]:
    try:
        from watch.rules.store import apply_alert_action  # import tardif : la veille peut être absente
    except Exception:  # noqa: BLE001
        return False, "action indisponible (module de veille absent)"
    try:
        result = await apply_alert_action(alert_id, action, actor="telegram")
    except Exception as exc:  # noqa: BLE001
        log.warning("apply_alert_action(%s, %s) : %s", alert_id, action, exc)
        return False, f"échec : {str(exc)[:80]}"
    if result is None:
        return False, "alerte inconnue ou déjà traitée"
    return True, f"Alerte {action}"


@router.post("/webhook")
async def webhook(request: Request,
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_secret(x_telegram_bot_api_secret_token)
    settings = get_telegram_settings()
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": True, "ignored": True, "reason": "corps illisible"}
    if not isinstance(update, dict):
        return {"ok": True, "ignored": True}

    senders = _sender_ids(update)
    allowed = settings.allowed_chat_ids
    if not senders or not senders.issubset(allowed):
        log.warning("Telegram : update ignoré, expéditeur non autorisé %s", sorted(senders))
        return {"ok": True, "ignored": True, "reason": "chat non autorisé"}

    svc = get_service()
    cq = update.get("callback_query")
    if cq:
        data = str(cq.get("data") or "")
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "alert" and parts[2] in VALID_ACTIONS:
            ok, text = await _apply_action(parts[1], parts[2])
            await svc.answer_callback(str(cq.get("id", "")), text)
            msg = cq.get("message") or {}
            if ok and msg.get("message_id") is not None:
                await svc.remove_buttons((msg.get("chat") or {}).get("id"), int(msg["message_id"]))
            return {"ok": True, "applied": ok, "detail": text}
        await svc.answer_callback(str(cq.get("id", "")), "commande inconnue")
        return {"ok": True, "applied": False}

    msg = update.get("message") or {}
    text = str(msg.get("text") or "").strip()
    if text.startswith("/status"):
        await svc.send_text("watch" if str((msg.get("chat") or {}).get("id")) == settings.telegram_chat_id_watch else "trading",
                            _status_text(), priority=True)
    return {"ok": True}


def _status_text() -> str:
    try:
        from etoro.api import _deps  # noqa: PLC0415

        store = _deps.store
        if store is None:
            return "CryptoMind : ok (module eToro non câblé)"
        snap = store.snapshot() if hasattr(store, "snapshot") else {}
        return (f"CryptoMind : ok\nkill switch : {'ACTIF' if snap.get('kill_switch') else 'non'}\n"
                f"breaker : {snap.get('breaker_until') or 'non'}\nPnL jour : {snap.get('daily_pnl', 0)}")
    except Exception:  # noqa: BLE001
        return "CryptoMind : ok"
