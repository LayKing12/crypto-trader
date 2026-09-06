"""Persistance async des règles, alertes, snapshots et Fear & Greed.

    store = RulesStore(session_factory)          # async_sessionmaker[AsyncSession]
    configure(session_factory)                   # pour `apply_alert_action` module (webhook Telegram)

Le store ne connaît ni eToro, ni Telegram, ni le risk guard : il lit et écrit les tables
`WatchBase`. Les exceptions métier (`AlertNotFound`, `AlertAlreadyHandled`, `RuleNotFound`,
`ValueError` de validation) sont traduites en 404 / 409 / 422 par `watch/api.py`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import schemas
from .models import (
    ALERT_STATUSES,
    TERMINAL_STATUSES,
    WatchAlert,
    WatchFngDaily,
    WatchPortfolioSnapshot,
    WatchRule,
    as_utc,
    utcnow,
)

log = logging.getLogger(__name__)

SNAPSHOT_MIN_INTERVAL_S = 3600.0
ACTIONS: tuple[str, ...] = ("executed", "ignored", "postponed")


class RuleNotFound(LookupError):
    pass


class AlertNotFound(LookupError):
    pass


class AlertAlreadyHandled(RuntimeError):
    def __init__(self, alert: dict[str, Any]) -> None:
        super().__init__(f"alerte déjà traitée ({alert.get('status')})")
        self.alert = alert


class RulesStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------ règles
    async def create_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        """`data` = body du POST (family, name, enabled, symbol, params). Lève ValueError si invalide."""
        try:
            body = schemas.RuleCreate.model_validate(data)
        except Exception as exc:  # pydantic.ValidationError -> ValueError lisible
            raise ValueError(str(exc)) from exc
        rule = WatchRule(
            family=body.family, name=body.name, enabled=body.enabled, symbol=body.symbol,
            params=body.params, state=_initial_state(body.family, body.params),
        )
        async with self._sf() as session:
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            return rule.to_dict()

    async def list_rules(self, enabled: bool | None = None, family: str | None = None) -> list[dict[str, Any]]:
        stmt = select(WatchRule).order_by(WatchRule.created_at.asc(), WatchRule.id.asc())
        if enabled is not None:
            stmt = stmt.where(WatchRule.enabled.is_(enabled))
        if family:
            stmt = stmt.where(WatchRule.family == family)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [r.to_dict() for r in rows]

    async def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            rule = await session.get(WatchRule, rule_id)
            return rule.to_dict() if rule is not None else None

    async def update_rule(self, rule_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Patch partiel. Lève RuleNotFound ou ValueError (params invalides après fusion)."""
        try:
            body = schemas.RuleUpdate.model_validate(patch or {})
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        clean = body.model_dump(exclude_unset=True)
        async with self._sf() as session:
            rule = await session.get(WatchRule, rule_id)
            if rule is None:
                raise RuleNotFound(rule_id)
            merged = schemas.merge_rule_update(rule.to_dict(), clean)
            params_changed = merged["params"] != (rule.params or {})
            family_changed = merged["family"] != rule.family
            rule.family = merged["family"]
            rule.name = merged["name"]
            rule.enabled = bool(merged["enabled"])
            rule.symbol = merged["symbol"]
            rule.params = merged["params"]
            if family_changed or params_changed:
                rule.state = _initial_state(rule.family, rule.params)  # réarmement sur changement de paramètres
            rule.updated_at = utcnow()
            await session.commit()
            await session.refresh(rule)
            return rule.to_dict()

    async def set_rule_state(self, rule_id: str, state: dict[str, Any]) -> None:
        async with self._sf() as session:
            rule = await session.get(WatchRule, rule_id)
            if rule is None:
                return
            rule.state = dict(state or {})
            await session.commit()

    async def delete_rule(self, rule_id: str) -> bool:
        async with self._sf() as session:
            rule = await session.get(WatchRule, rule_id)
            if rule is None:
                return False
            await session.delete(rule)
            await session.commit()
            return True

    # ------------------------------------------------------------------ alertes
    async def create_alert(self, alert: dict[str, Any], telegram_message_id: int | None = None) -> dict[str, Any]:
        row = WatchAlert(
            rule_id=str(alert.get("rule_id") or ""),
            family=str(alert.get("family") or ""),
            rule_name=str(alert.get("rule_name") or ""),
            symbol=alert.get("symbol"),
            title=str(alert.get("title") or ""),
            detail=dict(alert.get("detail") or {}),
            status="pending",
            telegram_message_id=telegram_message_id,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def set_alert_telegram_id(self, alert_id: str, message_id: int | None) -> None:
        if message_id is None:
            return
        async with self._sf() as session:
            row = await session.get(WatchAlert, alert_id)
            if row is None:
                return
            row.telegram_message_id = int(message_id)
            await session.commit()

    async def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(WatchAlert, alert_id)
            if row is None:
                return None
            if _wake_if_expired(row, utcnow()):
                await session.commit()
            return row.to_dict()

    async def wake_postponed(self, now: datetime | None = None) -> int:
        """Les `postponed` dont `postponed_until` est passé redeviennent `pending`."""
        now = now or utcnow()
        async with self._sf() as session:
            rows = (await session.execute(
                select(WatchAlert).where(WatchAlert.status == "postponed")
            )).scalars().all()
            woken = sum(1 for r in rows if _wake_if_expired(r, now))
            if woken:
                await session.commit()
            return woken

    async def list_alerts(self, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
        """Plus récent en premier. `status="all"` ou None pour tout."""
        await self.wake_postponed()
        limit = max(1, min(1000, int(limit or 50)))
        stmt = select(WatchAlert).order_by(WatchAlert.created_at.desc(), WatchAlert.id.desc()).limit(limit)
        if status and status != "all":
            if status not in ALERT_STATUSES:
                raise ValueError(f"status inconnu : {status!r}")
            stmt = stmt.where(WatchAlert.status == status)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [r.to_dict() for r in rows]

    async def apply_alert_action(self, alert_id: str, action: str, actor: str = "ui",
                                 postpone_hours: float = 24.0) -> dict[str, Any]:
        """executed / ignored : terminal. postponed : `postponed_until` = now + heures.

        Lève AlertNotFound (404) ou AlertAlreadyHandled (409, alerte déjà executed/ignored).
        Une alerte `postponed` peut être reprise avant l'échéance ; expirée, elle est d'abord réveillée.
        """
        if action not in ACTIONS:
            raise ValueError(f"action inconnue : {action!r} (attendu : {', '.join(ACTIONS)})")
        try:
            hours = float(postpone_hours)
        except (TypeError, ValueError):
            hours = 24.0
        if hours <= 0:
            raise ValueError("postpone_hours doit être > 0")
        now = utcnow()
        async with self._sf() as session:
            row = await session.get(WatchAlert, alert_id)
            if row is None:
                raise AlertNotFound(alert_id)
            _wake_if_expired(row, now)
            if row.status in TERMINAL_STATUSES:
                raise AlertAlreadyHandled(row.to_dict())
            row.status = action
            row.acted_at = now
            row.acted_by = (actor or "ui")[:32]
            row.postponed_until = now + timedelta(hours=hours) if action == "postponed" else None
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    # ------------------------------------------------------------------ snapshots
    async def latest_snapshot(self) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(
                select(WatchPortfolioSnapshot).order_by(WatchPortfolioSnapshot.at.desc()).limit(1)
            )).scalar_one_or_none()
            return row.to_dict() if row is not None else None

    async def add_snapshot(self, snapshot: dict[str, Any], at: datetime | None = None,
                           min_interval_s: float = SNAPSHOT_MIN_INTERVAL_S) -> dict[str, Any] | None:
        """Persiste un snapshot {total_usd, categories, positions} ; None si le dernier date de moins d'1 h."""
        at = as_utc(at) or utcnow()
        async with self._sf() as session:
            last = (await session.execute(
                select(WatchPortfolioSnapshot).order_by(WatchPortfolioSnapshot.at.desc()).limit(1)
            )).scalar_one_or_none()
            if last is not None and min_interval_s > 0:
                last_at = as_utc(last.at)
                if last_at is not None and (at - last_at).total_seconds() < min_interval_s:
                    return None
            row = WatchPortfolioSnapshot(
                at=at,
                total_usd=float(snapshot.get("total_usd") or 0.0),
                categories=list(snapshot.get("categories") or []),
                positions=dict(snapshot.get("positions") or {}),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def history(self, days: int = 30) -> list[dict[str, Any]]:
        """Points {at, value_usd} du plus ancien au plus récent sur `days` jours."""
        days = max(1, int(days))
        since = utcnow() - timedelta(days=days)
        async with self._sf() as session:
            rows = (await session.execute(
                select(WatchPortfolioSnapshot).where(WatchPortfolioSnapshot.at >= since)
                .order_by(WatchPortfolioSnapshot.at.asc())
            )).scalars().all()
            return [{"at": r.to_dict()["at"], "value_usd": float(r.total_usd or 0.0)} for r in rows]

    snapshot_history = history

    # ------------------------------------------------------------------ Fear & Greed
    async def upsert_daily(self, entries: list[dict[str, Any]]) -> int:
        """Insère/met à jour les entrées {date, value, classification}. Retourne le nombre écrit."""
        written = 0
        async with self._sf() as session:
            for entry in entries or []:
                day = str(entry.get("date") or "")[:10]
                try:
                    value = int(round(float(entry.get("value"))))
                except (TypeError, ValueError):
                    continue
                if len(day) != 10:
                    continue
                classification = str(entry.get("classification") or "")[:32]
                row = await session.get(WatchFngDaily, day)
                if row is None:
                    session.add(WatchFngDaily(date=day, value=value, classification=classification))
                else:
                    row.value = value
                    row.classification = classification
                    row.fetched_at = utcnow()
                written += 1
            if written:
                await session.commit()
        return written

    async def fng_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Du plus récent au plus ancien."""
        limit = max(1, min(1000, int(limit or 30)))
        async with self._sf() as session:
            rows = (await session.execute(
                select(WatchFngDaily).order_by(WatchFngDaily.date.desc()).limit(limit)
            )).scalars().all()
            return [r.to_dict() for r in rows]


# ---------------------------------------------------------------------- helpers

def _initial_state(family: str, params: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {"condition_active": False, "last_fired_at": None, "consecutive_days": 0}
    if family == "take_profit":
        state["levels_fired"] = [False] * len(params.get("levels") or [])
    return state


def _wake_if_expired(row: WatchAlert, now: datetime) -> bool:
    if row.status != "postponed":
        return False
    until = as_utc(row.postponed_until)
    if until is None or until <= now:
        row.status = "pending"
        row.postponed_until = None
        return True
    return False


# ---------------------------------------------------------------------- accès module (webhook Telegram)

_session_factory: async_sessionmaker[AsyncSession] | None = None
_store: RulesStore | None = None


def configure(session_factory: async_sessionmaker[AsyncSession] | None) -> RulesStore | None:
    """Enregistre la session factory utilisée par `apply_alert_action` (module)."""
    global _session_factory, _store
    _session_factory = session_factory
    _store = RulesStore(session_factory) if session_factory is not None else None
    return _store


def get_store() -> RulesStore | None:
    return _store


async def apply_alert_action(alert_id: str, action: str, actor: str = "telegram",
                             postpone_hours: float = 24.0) -> dict[str, Any]:
    """Point d'entrée pour `notifications/api.py` (import tardif). Lève RuntimeError si non configuré."""
    if _store is None:
        raise RuntimeError("watch.rules.store non configuré (règles désactivées)")
    return await _store.apply_alert_action(alert_id, action, actor=actor, postpone_hours=postpone_hours)
