"""Garde-fous de risque de l'Agent Portfolio eToro.

Reprend le correctif appliqué côté Kraken après l'incident des 1599 trades/jour :
chaque ouverture passe par `check_open` ; chaque fermeture passe par `on_position_closed`
qui alimente le PnL du jour et arme le circuit breaker. Aucune règle n'a d'exception.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import ClosedPosition, OrderRequest, Position, RiskDecision, Side, Signal
from .state_store import StateStore

logger = logging.getLogger(__name__)

_EPS = 1e-9


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _deny(reason: str) -> RiskDecision:
    return RiskDecision(allowed=False, reason=reason)


class RiskGuard:
    """Applique les règles de risque dans un ordre fixe, avec des `reason` stables."""

    def __init__(self, settings: Settings, store: StateStore) -> None:
        self.settings = settings
        self.store = store

    # ------------------------------------------------------------ états

    def kill_switch_active(self) -> bool:
        """Vrai si l'agent est désactivé par la config ou par le kill switch persistant."""
        return (not self.settings.etoro_agent_enabled) or bool(self.store.kill_switch)

    def is_breaker_active(self, now: datetime | None = None) -> bool:
        """Vrai tant que `now` est strictement avant `store.breaker_until`."""
        until = self.store.breaker_until
        if until is None:
            return False
        return _as_utc(now) < _as_utc(until)

    # ---------------------------------------------------------- ouverture

    def check_open(
        self,
        signal: Signal,
        order: OrderRequest,
        open_positions: list[Position],
        equity: float,
        now: datetime | None = None,
    ) -> RiskDecision:
        """Vérifie une ouverture. Ordre des règles : voir CONTRACT.md."""
        s = self.settings
        ts = _as_utc(now)

        if self.kill_switch_active():
            return _deny("kill_switch")
        if self.is_breaker_active(ts):
            return _deny("breaker_active")
        if s.real_mode_locked:
            return _deny("real_mode_without_rotation")
        if signal.market_score < s.etoro_min_score:
            return _deny("score_below_min")

        sl, tp, entry = order.stop_loss_rate, order.take_profit_rate, order.entry_rate
        if sl is None or tp is None:
            return _deny("missing_sl_tp")
        if order.side == Side.BUY:
            sl_tp_ok = sl < entry < tp
        else:
            sl_tp_ok = tp < entry < sl
        if not sl_tp_ok:
            return _deny("invalid_sl_tp")

        if order.leverage > s.etoro_max_leverage:
            return _deny("leverage_too_high")
        max_amount = float(equity) * s.etoro_position_size_pct / 100.0 * 1.01
        if order.amount > max_amount:
            return _deny("amount_too_large")

        if signal.symbol.strip().upper() not in s.universe:
            return _deny("instrument_not_in_universe")

        # Positions ouvertes tous instruments confondus : union broker + état local.
        known_ids = {p.position_id for p in open_positions}
        known_ids.update(self.store.open_positions_by_instrument.values())
        if len(known_ids) >= s.etoro_max_open_positions:
            return _deny("max_positions_reached")

        instrument_id = signal.instrument_id
        if instrument_id in self.store.open_positions_by_instrument or any(
            p.instrument_id == instrument_id for p in open_positions
        ):
            return _deny("position_already_open")

        last = self.store.last_trade_at.get(instrument_id)
        if last is not None and s.etoro_cooldown_hours > 0:
            elapsed = ts - _as_utc(last)
            if elapsed < timedelta(hours=s.etoro_cooldown_hours):
                return _deny("cooldown_active")

        return RiskDecision(allowed=True, reason="ok")

    # ---------------------------------------------------------- fermeture

    def on_position_closed(self, closed: ClosedPosition, now: datetime | None = None) -> RiskDecision:
        """Met à jour le PnL jour, libère l'instrument, arme le breaker si la perte dépasse la limite."""
        ts = _as_utc(now)
        store = self.store
        store.daily_pnl = float(store.daily_pnl) + float(closed.realized_pnl)
        if store.daily_pnl_date is None:
            store.daily_pnl_date = ts.date()
        store.record_close(closed.instrument_id, ts)  # persiste aussi daily_pnl

        if store.daily_pnl >= 0:
            return RiskDecision(allowed=True, reason="ok")

        loss = -store.daily_pnl
        base = float(store.equity_start_of_day)
        if base > 0:
            tripped = (loss / base) * 100.0 >= self.settings.etoro_daily_loss_limit_pct - _EPS
        else:
            # Pas d'equity de référence : impossible de mesurer, on ferme par sécurité.
            tripped = True
            logger.warning("risk_guard: equity_start_of_day absente, breaker armé par sécurité")

        if not tripped:
            return RiskDecision(allowed=True, reason="ok")

        new_until = ts + timedelta(hours=self.settings.etoro_breaker_pause_hours)
        if store.breaker_until is None or _as_utc(store.breaker_until) < new_until:
            store.breaker_until = new_until
        store.save()
        logger.warning(
            "risk_guard: breaker armé jusqu'à %s (pnl jour=%.2f, equity départ=%.2f)",
            store.breaker_until.isoformat(), store.daily_pnl, base,
        )
        return _deny("breaker_tripped")
