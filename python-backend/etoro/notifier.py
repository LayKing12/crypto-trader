"""Notifications du module eToro via Telegram (chat « trading »).

- un message par ouverture / fermeture de position, un résumé quotidien ;
- alerte immédiate si le circuit breaker saute ou si le kill switch est actionné (prioritaire,
  jamais filtrée par l'anti-spam) ;
- no-op + log si Telegram n'est pas configuré (TELEGRAM_ENABLED / TELEGRAM_BOT_TOKEN /
  TELEGRAM_CHAT_ID_TRADING absents) ; ne lève jamais vers l'appelant.

Twilio a été retiré : Telegram est gratuit et permet les boutons inline pour la veille.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import Settings
from .models import ClosedPosition, DailyStats, Instrument, Position

log = logging.getLogger(__name__)

_MAX_LEN = 3500


class Notifier:
    def __init__(self, settings: Settings, telegram: Any = None) -> None:
        self._settings = settings
        self._telegram = telegram  # injectable (tests) ; sinon notifications.telegram_service.get_service()

    @property
    def prefix(self) -> str:
        return f"[CryptoMind eToro {self._settings.etoro_mode.upper()}]"

    def _service(self) -> Any:
        if self._telegram is None:
            try:
                from notifications.telegram_service import get_service  # import tardif

                self._telegram = get_service()
            except Exception:  # noqa: BLE001
                log.warning("Service Telegram indisponible", exc_info=True)
                return None
        return self._telegram

    @property
    def enabled(self) -> bool:
        svc = self._service()
        return bool(svc is not None and getattr(svc, "configured", False))

    # ------------------------------------------------------------------ envoi
    async def send_text(self, msg: str, *, priority: bool = False) -> bool:
        """Envoie le message (préfixé DEMO/REAL) sur le chat trading. True si parti."""
        body = f"{self.prefix} {msg}".strip()
        if len(body) > _MAX_LEN:
            body = body[: _MAX_LEN - 1] + "…"
        svc = self._service()
        if svc is None or not getattr(svc, "configured", False):
            log.info("Telegram non configuré, message ignoré : %s", body)
            return False
        try:
            message_id = await svc.send_text("trading", body, priority=priority)
            return message_id is not None
        except Exception:  # noqa: BLE001 - ne jamais lever vers l'appelant
            log.exception("Échec d'envoi Telegram : %s", body)
            return False

    # --------------------------------------------------------------- métiers
    async def send_position_opened(self, pos: Position, instrument: Instrument | None = None) -> None:
        symbol = instrument.symbol if instrument else f"#{pos.instrument_id}"
        sl = f"{pos.stop_loss_rate:.2f}" if pos.stop_loss_rate is not None else "-"
        tp = f"{pos.take_profit_rate:.2f}" if pos.take_profit_rate is not None else "-"
        await self.send_text(
            f"📈 Ouverture {pos.side.value} {symbol} {pos.amount:.0f} USD @ {pos.open_rate:.2f} SL {sl} TP {tp}"
        )

    async def send_position_closed(self, closed: ClosedPosition) -> None:
        sign = "+" if closed.realized_pnl >= 0 else ""
        await self.send_text(
            f"📉 Fermeture #{closed.instrument_id} position {closed.position_id} PnL {sign}{closed.realized_pnl:.2f} USD"
        )

    async def send_daily_summary(self, stats: DailyStats) -> None:
        sign = "+" if stats.realized_pnl >= 0 else ""
        await self.send_text(
            f"🗓 Résumé {stats.date} : {stats.trades_opened} ouvertures, {stats.trades_closed} fermetures, "
            f"PnL {sign}{stats.realized_pnl:.2f} USD ({sign}{stats.realized_pnl_pct:.2f} %), "
            f"{stats.open_positions} position(s) ouverte(s)"
            + (", breaker ACTIF" if stats.breaker_active else "")
            + (", kill switch ACTIF" if stats.kill_switch else "")
        )

    async def send_breaker_tripped(self, daily_pnl_pct: float) -> None:
        await self.send_text(
            f"🚨 Circuit breaker déclenché : perte du jour {daily_pnl_pct:.2f} %. Trading en pause 24h.",
            priority=True,
        )

    async def send_kill_switch(self, actor: str, enabled: bool) -> None:
        state = "ACTIVÉ" if enabled else "désactivé"
        await self.send_text(f"🛑 Kill switch {state} par {actor}", priority=True)
