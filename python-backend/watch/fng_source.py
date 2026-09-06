"""Fear & Greed (alternative.me) : source sans clé, cache 1 h, ne lève jamais.

    history = await fetch_fng(client, limit=30)
    -> [{"date": "YYYY-MM-DD", "value": 25, "classification": "Extreme Fear"}, ...] (plus récent en premier)

En cas d'erreur réseau / format, on renvoie le dernier cache connu (même périmé), sinon [].
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .rules.engine import classify_fng, zone_label

log = logging.getLogger(__name__)

FNG_URL = "https://api.alternative.me/fng/"
CACHE_TTL_S = 3600.0

_cache: dict[str, Any] = {"at": 0.0, "limit": 0, "data": []}


def reset_cache() -> None:
    _cache.update({"at": 0.0, "limit": 0, "data": []})


def cache_age_s() -> float | None:
    return (time.monotonic() - _cache["at"]) if _cache["at"] else None


def _parse_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        value = int(round(float(item.get("value"))))
    except (TypeError, ValueError):
        return None
    ts = item.get("timestamp")
    day: str | None = None
    try:
        if ts is not None:
            day = datetime.fromtimestamp(int(float(ts)), tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        day = None
    if day is None:
        raw_date = item.get("date")
        day = str(raw_date)[:10] if raw_date else None
    if not day:
        return None
    classification = item.get("value_classification") or zone_label(classify_fng(value))
    return {"date": day, "value": max(0, min(100, value)), "classification": str(classification)}


def parse_payload(payload: Any) -> list[dict[str, Any]]:
    """Décode la réponse alternative.me ; tolérant, sans exception."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        entry = _parse_entry(item)
        if entry is None or entry["date"] in seen:
            continue
        seen.add(entry["date"])
        out.append(entry)
    out.sort(key=lambda e: e["date"], reverse=True)
    return out


async def fetch_fng(client: httpx.AsyncClient | None, limit: int = 30, force: bool = False) -> list[dict[str, Any]]:
    """Historique Fear & Greed, plus récent en premier. Cache 1 h. Ne lève jamais."""
    limit = max(1, min(365, int(limit or 30)))
    now = time.monotonic()
    fresh = _cache["at"] and (now - _cache["at"]) < CACHE_TTL_S and _cache["limit"] >= limit
    if fresh and not force:
        return list(_cache["data"][:limit])
    own_client = client is None
    try:
        if own_client:
            client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        resp = await client.get(FNG_URL, params={"limit": limit, "format": "json"})
        resp.raise_for_status()
        data = parse_payload(resp.json())
        if not data:
            raise ValueError("réponse Fear & Greed vide ou inexploitable")
        _cache.update({"at": now, "limit": limit, "data": data})
        return list(data[:limit])
    except Exception as exc:  # noqa: BLE001 - jamais d'exception vers l'appelant
        log.warning("watch fng : récupération impossible (%s) ; cache=%d entrée(s)", exc, len(_cache["data"]))
        return list(_cache["data"][:limit])
    finally:
        if own_client and client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
