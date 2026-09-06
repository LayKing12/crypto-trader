"""Contexte de portefeuille : eToro (positions + equity) + Kraken (crypto), par catégorie.

    snapshot = await collect_snapshot(etoro_service, kraken_balance_fn, targets, symbols)
    -> {"at", "total_usd", "categories": [...], "positions": {symbol: {...}}, "allocation": {cat: {...}}}

Catégories par défaut : `crypto` (Kraken), `gold_miners` (NEM, AEM), `stocks` (autres eToro),
`cash` (equity eToro − positions). `targets` = {category: target_pct} (règles allocation_drift actives).
Tolérant aux erreurs : une source absente ou en échec compte pour 0, jamais d'exception.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

CATEGORIES: tuple[str, ...] = ("crypto", "stocks", "gold_miners", "cash")
GOLD_MINERS: frozenset[str] = frozenset({"NEM", "AEM"})
DEFAULT_ETORO_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "NEM", "AEM")

KrakenBalanceFn = Callable[[], Awaitable[Mapping[str, float] | None]]


def category_for(symbol: str, source: str = "etoro") -> str:
    if source == "kraken":
        return "crypto"
    return "gold_miners" if symbol.upper() in GOLD_MINERS else "stocks"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f else default


async def _etoro_symbols_by_id(etoro_service: Any, symbols: list[str]) -> dict[int, str]:
    if etoro_service is None or not symbols:
        return {}
    try:
        instruments = await etoro_service.get_instruments(list(symbols))
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : instruments eToro indisponibles (%s)", exc)
        return {}
    out: dict[int, str] = {}
    for inst in instruments or []:
        try:
            out[int(getattr(inst, "instrument_id"))] = str(getattr(inst, "symbol")).upper()
        except (TypeError, ValueError, AttributeError):
            continue
    return out


async def _etoro_positions(etoro_service: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Agrège les positions eToro par symbole : units, pru (moyen pondéré), value_usd."""
    if etoro_service is None:
        return {}
    try:
        raw_positions = await etoro_service.get_open_positions()
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : positions eToro indisponibles (%s)", exc)
        return {}
    ids = await _etoro_symbols_by_id(etoro_service, symbols)
    out: dict[str, dict[str, Any]] = {}
    for pos in raw_positions or []:
        try:
            instrument_id = int(getattr(pos, "instrument_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        symbol = ids.get(instrument_id) or f"ETORO:{instrument_id}"
        amount = _f(getattr(pos, "amount", 0.0))
        open_rate = _f(getattr(pos, "open_rate", 0.0))
        pnl = _f(getattr(pos, "unrealized_pnl", 0.0))
        units = amount / open_rate if open_rate > 0 else 0.0
        entry = out.setdefault(symbol, {
            "units": 0.0, "pru": None, "value_usd": 0.0, "invested_usd": 0.0,
            "category": category_for(symbol), "source": "etoro", "positions": 0,
        })
        entry["units"] += units
        entry["invested_usd"] += amount
        entry["value_usd"] += amount + pnl
        entry["positions"] += 1
    for entry in out.values():
        entry["pru"] = (entry["invested_usd"] / entry["units"]) if entry["units"] > 0 else None
        for key in ("units", "value_usd", "invested_usd"):
            entry[key] = round(entry[key], 8)
        if entry["pru"] is not None:
            entry["pru"] = round(entry["pru"], 8)
    return out


async def _etoro_equity(etoro_service: Any) -> float | None:
    if etoro_service is None:
        return None
    try:
        return _f(await etoro_service.get_account_balance())
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : equity eToro indisponible (%s)", exc)
        return None


async def _kraken_positions(kraken_balance_fn: KrakenBalanceFn | None) -> dict[str, dict[str, Any]]:
    if kraken_balance_fn is None:
        return {}
    try:
        balances = await kraken_balance_fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : soldes Kraken indisponibles (%s)", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for symbol, value in (balances or {}).items():
        v = _f(value)
        if v <= 0:
            continue
        out[str(symbol).upper()] = {
            "units": None, "pru": None, "value_usd": round(v, 8),
            "category": "crypto", "source": "kraken", "positions": 1,
        }
    return out


def build_categories(values: Mapping[str, float], targets: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """[{category, value_usd, actual_pct, target_pct | None, delta_points | None}] dans l'ordre canonique."""
    total = sum(max(0.0, _f(v)) for v in values.values())
    targets = targets or {}
    out: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        value = max(0.0, _f(values.get(cat, 0.0)))
        actual = (value / total * 100.0) if total > 0 else 0.0
        target = targets.get(cat)
        target_f = _f(target) if target is not None else None
        out.append({
            "category": cat,
            "value_usd": round(value, 2),
            "actual_pct": round(actual, 2),
            "target_pct": target_f,
            "delta_points": round(actual - target_f, 2) if target_f is not None else None,
        })
    return out


async def collect_snapshot(
    etoro_service: Any,
    kraken_balance_fn: KrakenBalanceFn | None = None,
    targets: Mapping[str, float] | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Snapshot complet du portefeuille. Ne lève jamais ; source absente = 0."""
    symbols = list(symbols) if symbols else list(DEFAULT_ETORO_SYMBOLS)
    positions: dict[str, dict[str, Any]] = {}
    try:
        positions.update(await _etoro_positions(etoro_service, symbols))
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : eToro ignoré (%s)", exc)
    try:
        for symbol, entry in (await _kraken_positions(kraken_balance_fn)).items():
            positions.setdefault(symbol, entry)
    except Exception as exc:  # noqa: BLE001
        log.warning("watch portfolio : Kraken ignoré (%s)", exc)

    values: dict[str, float] = {cat: 0.0 for cat in CATEGORIES}
    etoro_invested = 0.0
    for entry in positions.values():
        values[entry.get("category", "stocks")] += _f(entry.get("value_usd"))
        if entry.get("source") == "etoro":
            etoro_invested += _f(entry.get("value_usd"))
    equity = await _etoro_equity(etoro_service)
    if equity is not None:
        values["cash"] = max(0.0, equity - etoro_invested)

    categories = build_categories(values, targets)
    total = round(sum(c["value_usd"] for c in categories), 2)
    allocation = {c["category"]: {"value_usd": c["value_usd"], "actual_pct": c["actual_pct"]} for c in categories}
    return {
        "at": datetime.now(UTC).isoformat(),
        "total_usd": total,
        "categories": categories,
        "positions": positions,
        "allocation": allocation,
        "etoro_equity_usd": equity,
    }
