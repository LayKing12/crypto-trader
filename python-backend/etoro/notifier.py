"""Notifications SMS Twilio pour le module eToro.

Règles :
- no-op + log si Twilio n'est pas configuré (settings.twilio_configured False) ;
- ne lève JAMAIS vers l'appelant : toute erreur est loggée ;
- l'envoi Twilio est bloquant, il est exécuté dans un thread (asyncio.to_thread) ;
- anti-spam : au plus MAX_SMS_PER_HOUR SMS par heure glissante, sauf messages prioritaires
  (breaker, kill switch) qui passent toujours.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable

from .config import Settings
from .models import ClosedPosition, DailyStats, Instrument, Position

log = logging.getLogger(__name__)

MAX_SMS_PER_HOUR = 30
_WINDOW_S = 3600.0
_MAX_SMS_LEN = 320  # deux segments SMS GSM-7 max, on tronque au-delà


class Notifier:
    """Envoi de SMS courts en français via Twilio (client injectable pour les tests)."""

    def __init__(self, settings: Settings, client: Any = None, clock: Callable[[], float] | None = None):
        self._settings = settings
        self._client = client
        self._clock = clock or time.monotonic
        self._sent: deque[float] = deque()

    # ------------------------------------------------------------------ util
    @property
    def prefix(self) -> str:
        mode = "REAL" if self._settings.etoro_mode == "real" else "DEMO"
        return f"[CryptoMind eToro {mode}]"

    @property
    def enabled(self) -> bool:
        return bool(self._settings.twilio_configured)

    def _get_client(self) -> Any:
        """Construit paresseusement le client Twilio (seulement si configuré)."""
        if self._client is None and self.enabled:
            from twilio.rest import Client  # import tardif : dépendance optionnelle au runtime

            self._client = Client(self._settings.twilio_account_sid, self._settings.twilio_auth_token)
        return self._client

    def _rate_limited(self) -> bool:
        now = self._clock()
        while self._sent and now - self._sent[0] > _WINDOW_S:
            self._sent.popleft()
        return len(self._sent) >= MAX_SMS_PER_HOUR

    # ------------------------------------------------------------------ envoi
    async def _relay_telegram(self, body: str) -> None:
        """Relais optionnel vers Telegram (chat trading). No-op si non configuré, ne lève jamais."""
        relay = getattr(self, "telegram_relay", None)
        try:
            if relay is None:
                from notifications.telegram_service import get_service  # import tardif

                svc = get_service()
                if not svc.configured:
                    return
                relay = svc.send_trading_event
            await relay(body)
        except Exception:  # noqa: BLE001
            log.warning("Relais Telegram en échec (ignoré)", exc_info=True)

    async def send_text(self, msg: str, *, priority: bool = False) -> bool:
        """Envoie un SMS brut (préfixé DEMO/REAL) et relaie vers Telegram. Retourne True si le SMS est parti."""
        body = f"{self.prefix} {msg}".strip()
        await self._relay_telegram(body)
        if len(body) > _MAX_SMS_LEN:
            body = body[: _MAX_SMS_LEN - 1] + "…"
        if not self.enabled:
            log.info("Twilio non configuré, SMS ignoré : %s", body)
            return False
        if not priority and self._rate_limited():
            log.warning("Anti-spam SMS : limite de %d/h atteinte, message ignoré : %s", MAX_SMS_PER_HOUR, body)
            return False
        try:
            client = self._get_client()
            if client is None:
                log.warning("Client Twilio indisponible, SMS ignoré : %s", body)
                return False
            await asyncio.to_thread(
                client.messages.create,
                body=body,
                from_=self._settings.twilio_from,
                to=self._settings.twilio_to,
            )
            self._sent.append(self._clock())
            log.info("SMS envoyé : %s", body)
            return True
        except Exception:  # noqa: BLE001 - ne jamais lever vers l'appelant
            log.exception("Échec d'envoi SMS Twilio : %s", body)
            return False

    # --------------------------------------------------------------- métiers
    async def send_position_opened(self, pos: Position, instrument: Instrument | None = None) -> None:
        symbol = instrument.symbol if instrument else f"#{pos.instrument_id}"
        sl = f"{pos.stop_loss_rate:.2f}" if pos.stop_loss_rate is not None else "-"
        tp = f"{pos.take_profit_rate:.2f}" if pos.take_profit_rate is not None else "-"
        await self.send_text(
            f"Ouverture {pos.side.value} {symbol} {pos.amount:.0f} USD @ {pos.open_rate:.2f} SL {sl} TP {tp}"
        )

    async def send_position_closed(self, closed: ClosedPosition) -> None:
        await self.send_text(
            f"Fermeture #{closed.instrument_id} pos {closed.position_id} PnL {closed.realized_pnl:+.2f} USD"
        )

    async def send_daily_summary(self, stats: DailyStats) -> None:
        flags = []
        if stats.breaker_active:
            flags.append("BREAKER")
        if stats.kill_switch:
            flags.append("KILL SWITCH")
        extra = f" [{', '.join(flags)}]" if flags else ""
        await self.send_text(
            f"Bilan {stats.date} : {stats.trades_opened} ouv. / {stats.trades_closed} ferm., "
            f"PnL {stats.realized_pnl:+.2f} USD ({stats.realized_pnl_pct:+.2f}%), "
            f"{stats.open_positions} pos. ouvertes{extra}"
        )

    async def send_breaker_tripped(self, daily_pnl_pct: float) -> None:
        await self.send_text(
            f"BREAKER déclenché : PnL du jour {daily_pnl_pct:+.2f}%. Ouvertures suspendues "
            f"{self._settings.etoro_breaker_pause_hours:g} h.",
            priority=True,
        )

    async def send_kill_switch(self, actor: str, enabled: bool) -> None:
        state = "ACTIVÉ : ouvertures bloquées" if enabled else "désactivé : agent réactivé"
        await self.send_text(f"KILL SWITCH {state} (par {actor})", priority=True)
