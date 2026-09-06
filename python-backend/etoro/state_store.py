"""Persistance de l'état de l'Agent Portfolio eToro : fichier JSON atomique.

Contenu : positions ouvertes par instrument, date du dernier trade par instrument (cooldown),
PnL du jour, equity de début de journée, circuit breaker et kill switch.
Toutes les dates sont timezone-aware UTC. Chaque mutation via les méthodes `record_*`,
`set_kill_switch` et `reset_day` est persistée immédiatement.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    """Force une datetime en UTC timezone-aware (une naïve est supposée UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    return _ensure_utc(datetime.fromisoformat(str(value)))


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class StateStore:
    """État persistant de l'agent, sérialisé en JSON (write .tmp + os.replace)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._reset_state()

    # ------------------------------------------------------------------ état

    def _reset_state(self) -> None:
        self.open_positions_by_instrument: dict[int, str] = {}
        self.last_trade_at: dict[int, datetime] = {}
        self.daily_pnl: float = 0.0
        self.daily_pnl_date: date | None = None
        self.equity_start_of_day: float = 0.0
        self.breaker_until: datetime | None = None
        self.kill_switch: bool = False
        self.kill_switch_actor: str | None = None

    # ---------------------------------------------------------- persistance

    def load(self) -> None:
        """Charge l'état depuis le disque. Fichier absent ou corrompu => état vide + warning."""
        self._reset_state()
        if not self.path.exists():
            logger.info("state_store: aucun fichier d'état à %s, démarrage à vide", self.path)
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("le document JSON racine n'est pas un objet")
            self._apply(data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning(
                "state_store: fichier d'état %s illisible (%s) : repart d'un état vide", self.path, exc
            )
            self._reset_state()

    def _apply(self, data: dict[str, Any]) -> None:
        positions = data.get("open_positions_by_instrument") or {}
        self.open_positions_by_instrument = {int(k): str(v) for k, v in positions.items()}
        last = data.get("last_trade_at") or {}
        parsed_last: dict[int, datetime] = {}
        for k, v in last.items():
            dt = _parse_datetime(v)
            if dt is not None:
                parsed_last[int(k)] = dt
        self.last_trade_at = parsed_last
        self.daily_pnl = float(data.get("daily_pnl") or 0.0)
        self.daily_pnl_date = _parse_date(data.get("daily_pnl_date"))
        self.equity_start_of_day = float(data.get("equity_start_of_day") or 0.0)
        self.breaker_until = _parse_datetime(data.get("breaker_until"))
        self.kill_switch = bool(data.get("kill_switch", False))
        actor = data.get("kill_switch_actor")
        self.kill_switch_actor = str(actor) if actor is not None else None

    def _to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "open_positions_by_instrument": {
                str(k): v for k, v in sorted(self.open_positions_by_instrument.items())
            },
            "last_trade_at": {
                str(k): _ensure_utc(v).isoformat() for k, v in sorted(self.last_trade_at.items())
            },
            "daily_pnl": float(self.daily_pnl),
            "daily_pnl_date": self.daily_pnl_date.isoformat() if self.daily_pnl_date else None,
            "equity_start_of_day": float(self.equity_start_of_day),
            "breaker_until": _ensure_utc(self.breaker_until).isoformat() if self.breaker_until else None,
            "kill_switch": bool(self.kill_switch),
            "kill_switch_actor": self.kill_switch_actor,
            "saved_at": _utc_now().isoformat(),
        }

    def save(self) -> None:
        """Écriture atomique : dossier parent créé si absent, fichier .tmp puis os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        payload = json.dumps(self._to_dict(), indent=2, sort_keys=True)
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                raise

    # ------------------------------------------------------------ mutations

    def reset_day(self, equity: float, today: date) -> None:
        """Nouvelle journée UTC : remet le PnL jour à zéro et fige l'equity de départ.

        Le breaker n'est pas levé ici : sa durée (`breaker_until`) est indépendante du jour.
        """
        self.daily_pnl = 0.0
        self.daily_pnl_date = today
        self.equity_start_of_day = float(equity)
        self.save()

    def record_open(self, instrument_id: int, position_id: str, now: datetime | None = None) -> None:
        """Enregistre l'ouverture d'une position et démarre le cooldown de l'instrument."""
        ts = _ensure_utc(now) if now else _utc_now()
        self.open_positions_by_instrument[int(instrument_id)] = str(position_id)
        self.last_trade_at[int(instrument_id)] = ts
        self.save()

    def record_close(self, instrument_id: int, now: datetime | None = None) -> None:
        """Enregistre la fermeture d'une position et relance le cooldown de l'instrument."""
        ts = _ensure_utc(now) if now else _utc_now()
        self.open_positions_by_instrument.pop(int(instrument_id), None)
        self.last_trade_at[int(instrument_id)] = ts
        self.save()

    def set_kill_switch(self, enabled: bool, actor: str | None = None) -> None:
        """Active/désactive le kill switch en traçant l'auteur."""
        self.kill_switch = bool(enabled)
        self.kill_switch_actor = actor
        self.save()

    # -------------------------------------------------------------- lecture

    def snapshot(self) -> dict[str, Any]:
        """Vue JSON-sérialisable de l'état (pour GET /status)."""
        data = self._to_dict()
        data.pop("saved_at", None)
        data["open_positions_count"] = len(self.open_positions_by_instrument)
        return data
