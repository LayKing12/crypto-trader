"""
WhatsApp notification service via Twilio.
Notifications 24h/24 avec throttle pour éviter le rate limit Twilio.
"""
from __future__ import annotations
import time
from twilio.rest import Client
from app.config import get_settings
from app.utils.logging_utils import get_logger

log = get_logger(__name__)
settings = get_settings()

_client: Client | None = None

# Throttle : max 1 notification d'ouverture par symbole toutes les 10 minutes
# Évite le flood Twilio lors des cycles rapides (1586 trades = silence forcé)
_last_open_notif: dict[str, float] = {}
OPEN_NOTIF_COOLDOWN = 600  # secondes


def _get_client() -> Client:
    global _client
    if _client is None:
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            raise RuntimeError("Twilio credentials manquants (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)")
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def send_message(body: str, urgent: bool = False) -> bool:
    """Send WhatsApp message. Returns True on success."""
    if not settings.whatsapp_recipient:
        log.warning("whatsapp_skip", reason="WHATSAPP_RECIPIENT not set")
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
    # Throttle : skip si ce symbole a déjà été notifié récemment
    now = time.time()
    last = _last_open_notif.get(symbol, 0)
    if now - last < OPEN_NOTIF_COOLDOWN:
        log.info("whatsapp_throttled", symbol=symbol, wait_s=int(OPEN_NOTIF_COOLDOWN - (now - last)))
        return False
    _last_open_notif[symbol] = now

    mode = "[PAPER]" if is_paper else "[LIVE]"
    sl_pct = round((stop_loss - price) / price * 100, 1)
    body = (
        f"🚀 {mode} ACHAT {symbol}\n"
        f"Prix: ${price:,.2f}\n"
        f"Taille: {size_pct:.1f}% du capital\n"
        f"Stop-loss: ${stop_loss:,.2f} ({sl_pct:+.1f}%)\n"
        f"TP: +3%/+5%/+8%/+15%"
    )
    return send_message(body)


def notify_trade_closed(symbol: str, pnl_pct: float, pnl_usd: float, result: str) -> bool:
    emoji = "✅" if result == "win" else "❌"
    body = (
        f"{emoji} CLÔTURE {symbol}\n"
        f"Résultat: {result.upper()}\n"
        f"P&L: {pnl_pct:+.2f}% (${pnl_usd:+,.2f})"
    )
    return send_message(body)


def notify_stop_loss_hit(symbol: str, price: float) -> bool:
    body = f"🛑 STOP-LOSS {symbol} @ ${price:,.2f}"
    return send_message(body, urgent=True)


def notify_take_profit_hit(symbol: str, price: float, pnl_pct: float) -> bool:
    body = f"🎯 TAKE-PROFIT {symbol} @ ${price:,.2f} ({pnl_pct:+.1f}%)"
    return send_message(body)


def notify_risk_alert(reason: str) -> bool:
    body = f"⚠️ ALERTE RISQUE\n{reason}"
    return send_message(body, urgent=True)
