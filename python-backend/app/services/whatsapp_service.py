"""
WhatsApp notification service via Twilio.
Mirrors the existing Node.js whatsapp.js service — notifications 24h/24.
"""
from __future__ import annotations
from twilio.rest import Client
from app.config import get_settings
from app.utils.logging_utils import get_logger

log = get_logger(__name__)
settings = get_settings()

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def _is_dnd(urgent: bool = False) -> bool:
    return False  # Notifications 24h/24 — DND désactivé


def send_message(body: str, urgent: bool = False) -> bool:
    """Send WhatsApp message. Returns True on success."""
    if _is_dnd(urgent):
        log.info("whatsapp_dnd_skip", urgent=urgent)
        return False

    try:
        client = _get_client()
        client.messages.create(
            body=body,
            from_=settings.twilio_whatsapp_from,
            to=settings.whatsapp_recipient,
        )
        log.info("whatsapp_sent", chars=len(body))
        return True
    except Exception as e:
        log.error("whatsapp_error", error=str(e))
        return False


def notify_trade_opened(symbol: str, price: float, size_pct: float, stop_loss: float, is_paper: bool) -> bool:
    mode = "[PAPER]" if is_paper else "[LIVE]"
    body = (
        f"🚀 {mode} ACHAT {symbol}\n"
        f"Prix: ${price:,.2f}\n"
        f"Taille: {size_pct:.1f}% du capital\n"
        f"Stop-loss: ${stop_loss:,.2f} (-7%)\n"
        f"TP: +50%/+100%/+150%/+200%"
    )
    return send_message(body)


def notify_trade_closed(symbol: str, pnl_pct: float, pnl_usd: float, result: str) -> bool:
    emoji = "✅" if result == "win" else "❌"
    body = (
        f"{emoji} CLÔTURE {symbol}\n"
        f"Résultat: {result.upper()}\n"
        f"P&L: {pnl_pct:+.2f}% (${pnl_usd:+,.2f})"
    )
    return send_message(body, urgent=False)


def notify_risk_alert(reason: str) -> bool:
    body = f"⚠️ ALERTE RISQUE\n{reason}"
    return send_message(body, urgent=True)


def notify_stop_loss_hit(symbol: str, price: float) -> bool:
    body = f"🛑 STOP-LOSS {symbol} @ ${price:,.2f}"
    return send_message(body, urgent=True)