"""Tables de la veille sur une `DeclarativeBase` propre (`WatchBase`).

Indépendantes de `app.database.Base` pour rester testables sur `sqlite+aiosqlite` sans asyncpg.
En production `watch/runtime.py` fait `WatchBase.metadata.create_all` via `app.database.engine`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB, "postgresql")

FAMILIES: tuple[str, ...] = ("take_profit", "allocation_drift", "sentiment_zone")
ALERT_STATUSES: tuple[str, ...] = ("pending", "executed", "ignored", "postponed")
TERMINAL_STATUSES: frozenset[str] = frozenset({"executed", "ignored"})


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite renvoie des datetimes naïfs : on les ré-étiquette UTC (tout est stocké en UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value is not None else None


class WatchBase(DeclarativeBase):
    pass


class WatchRule(WatchBase):
    __tablename__ = "watch_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    family: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict[str, Any]:
        state = dict(self.state or {})
        state.setdefault("condition_active", False)
        state.setdefault("last_fired_at", None)
        state.setdefault("consecutive_days", 0)
        return {
            "id": self.id,
            "family": self.family,
            "name": self.name,
            "enabled": bool(self.enabled),
            "symbol": self.symbol,
            "params": dict(self.params or {}),
            "state": state,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class WatchAlert(WatchBase):
    __tablename__ = "watch_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rule_id: Mapped[str] = mapped_column(String(36), index=True)
    family: Mapped[str] = mapped_column(String(32))
    rule_name: Mapped[str] = mapped_column(String(200))
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acted_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    postponed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "family": self.family,
            "rule_name": self.rule_name,
            "symbol": self.symbol,
            "title": self.title,
            "detail": dict(self.detail or {}),
            "status": self.status,
            "created_at": iso(self.created_at),
            "acted_at": iso(self.acted_at),
            "acted_by": self.acted_by,
            "postponed_until": iso(self.postponed_until),
            "telegram_message_id": self.telegram_message_id,
        }


class WatchPortfolioSnapshot(WatchBase):
    __tablename__ = "watch_portfolio_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    categories: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    positions: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": iso(self.at),
            "total_usd": float(self.total_usd or 0.0),
            "categories": list(self.categories or []),
            "positions": dict(self.positions or {}),
        }


class WatchFngDaily(WatchBase):
    __tablename__ = "watch_fng_daily"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    value: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "value": int(self.value), "classification": self.classification}
