"""Persistance Supabase (PostgREST) pour l'état du bot et le journal des décisions.

Pourquoi : les hébergeurs gratuits (Render, Koyeb...) n'ont pas de disque persistant. Sans
persistance externe, un redéploiement effacerait le circuit breaker, les cooldowns et le PnL
du jour, c'est-à-dire exactement les garde-fous. Les fichiers JSON locaux restent un cache.

Tables (voir docs/supabase_setup.md) :
    etoro_state(id text pk, data jsonb, updated_at timestamptz)
    etoro_decisions(id text pk, at timestamptz, kind text, symbol text, data jsonb)

Activation : SUPABASE_URL + SUPABASE_SERVICE_KEY (clé service role, jamais la clé anon côté bot).
Toutes les méthodes sont synchrones, courtes (timeout 5 s) et n'élèvent jamais d'exception
vers l'appelant : en cas de panne Supabase, le bot continue sur son cache local.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

STATE_TABLE = "etoro_state"
DECISIONS_TABLE = "etoro_decisions"
STATE_ROW_ID = "main"


class SupabaseStore:
    def __init__(self, url: str, service_key: str, timeout_s: float = 5.0,
                 client: httpx.Client | None = None) -> None:
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(timeout=timeout_s)
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Any) -> "SupabaseStore | None":
        url = getattr(settings, "supabase_url", None)
        key = getattr(settings, "supabase_service_key", None)
        if url and key:
            return cls(url, key)
        return None

    # ------------------------------------------------------------------ état
    def get_state(self) -> dict[str, Any] | None:
        try:
            r = self._client.get(f"{self._base}/{STATE_TABLE}", headers=self._headers,
                                 params={"id": f"eq.{STATE_ROW_ID}", "select": "data"})
            r.raise_for_status()
            rows = r.json()
            if rows and isinstance(rows[0].get("data"), dict):
                return rows[0]["data"]
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase get_state : %s", exc)
            return None

    def upsert_state(self, data: dict[str, Any]) -> bool:
        try:
            r = self._client.post(
                f"{self._base}/{STATE_TABLE}",
                headers={**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={"id": STATE_ROW_ID, "data": data},
            )
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase upsert_state : %s", exc)
            return False

    # ------------------------------------------------------------- décisions
    def insert_decision(self, rec: dict[str, Any]) -> bool:
        try:
            r = self._client.post(
                f"{self._base}/{DECISIONS_TABLE}",
                headers={**self._headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                json={"id": rec.get("id"), "at": rec.get("at"), "kind": rec.get("kind"),
                      "symbol": rec.get("symbol"), "data": rec},
            )
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase insert_decision : %s", exc)
            return False

    def recent_decisions(self, limit: int = 500) -> list[dict[str, Any]]:
        """Du plus récent au plus ancien."""
        try:
            r = self._client.get(f"{self._base}/{DECISIONS_TABLE}", headers=self._headers,
                                 params={"select": "data", "order": "at.desc", "limit": str(limit)})
            r.raise_for_status()
            return [row["data"] for row in r.json() if isinstance(row.get("data"), dict)]
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase recent_decisions : %s", exc)
            return []

    def health(self) -> bool:
        try:
            r = self._client.get(f"{self._base}/{STATE_TABLE}", headers=self._headers,
                                 params={"select": "id", "limit": "1"})
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
