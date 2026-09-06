"""Moteur de règles pur : aucune I/O, aucune base, aucun ordre.

    alert, new_state = evaluate(rule_dict, context)

`context` = {
    "prices":      {symbol: last},
    "positions":   {symbol: {"units", "pru", "value_usd"}},
    "allocation":  {category: {"value_usd", "actual_pct"}},
    "total_usd":   float,
    "fng_history": [{"date", "value", "classification"}],   # ordre quelconque
    "now":         datetime (UTC) | None,
}

Dédoublonnage sur la **transition** : on n'émet que lorsque la condition passe de faux à vrai
(`state.condition_active`). Pour `take_profit` l'état est par palier (`state.levels_fired`).
Pour `sentiment_zone` la condition est « zone atteinte N jours consécutifs » sur `fng_history`.
L'alerte renvoyée est un dict sans id/status/created_at : c'est le store qui les pose.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

FNG_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 24, "extreme_fear"),
    (25, 44, "fear"),
    (45, 55, "neutral"),
    (56, 75, "greed"),
    (76, 100, "extreme_greed"),
)
FNG_LABELS: dict[str, str] = {
    "extreme_fear": "Extreme Fear",
    "fear": "Fear",
    "neutral": "Neutral",
    "greed": "Greed",
    "extreme_greed": "Extreme Greed",
}


# ---------------------------------------------------------------------- utilitaires

def classify_fng(value: Any) -> str | None:
    """0-24 extreme_fear, 25-44 fear, 45-55 neutral, 56-75 greed, 76-100 extreme_greed."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    v = max(0, min(100, v))
    for lo, hi, zone in FNG_BANDS:
        if lo <= v <= hi:
            return zone
    return None


def zone_label(zone: str | None) -> str:
    return FNG_LABELS.get(zone or "", str(zone or "Unknown"))


def zone_from_label(label: Any) -> str | None:
    """« Extreme Fear » -> extreme_fear ; accepte déjà la forme snake_case."""
    if not isinstance(label, str) or not label.strip():
        return None
    key = label.strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in FNG_LABELS else None


def _now(context: dict[str, Any]) -> datetime:
    now = context.get("now")
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _base_state(rule: dict[str, Any]) -> dict[str, Any]:
    state = dict(rule.get("state") or {})
    state.setdefault("condition_active", False)
    state.setdefault("last_fired_at", None)
    state.setdefault("consecutive_days", 0)
    return state


def _alert(rule: dict[str, Any], title: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": rule.get("id"),
        "family": rule.get("family"),
        "rule_name": rule.get("name") or "",
        "symbol": rule.get("symbol"),
        "title": title,
        "detail": detail,
    }


def _fmt_price(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


# ---------------------------------------------------------------------- take_profit

def _eval_take_profit(rule: dict[str, Any], context: dict[str, Any]) -> tuple[dict | None, dict]:
    params = rule.get("params") or {}
    state = _base_state(rule)
    levels = list(params.get("levels") or [])
    fired = list(state.get("levels_fired") or [])
    fired = [bool(x) for x in fired[: len(levels)]] + [False] * max(0, len(levels) - len(fired))

    symbol = (rule.get("symbol") or "").upper()
    price = _float((context.get("prices") or {}).get(symbol))
    position = (context.get("positions") or {}).get(symbol) or {}
    pru = _float(params.get("pru"))
    if pru is None:
        pru = _float(position.get("pru"))

    if price is None or price <= 0:
        # Pas de prix : on ne change rien (ni tir, ni réarmement).
        state["levels_fired"] = fired
        state["condition_active"] = any(fired)
        return None, state

    alert: dict | None = None
    for i, level in enumerate(levels):
        target = _float(level.get("price"))
        if target is None:
            multiple = _float(level.get("multiple_of_pru"))
            if multiple is None or pru is None or pru <= 0:
                continue  # palier inexploitable sans PRU
            target = multiple * pru
        active = price >= target
        if not active:
            fired[i] = False  # réarmement du palier
            continue
        if fired[i] or alert is not None:
            continue  # déjà tiré, ou un autre palier est émis dans cette évaluation
        fired[i] = True
        sell_pct = _float(level.get("sell_pct")) or 0.0
        units = _float(position.get("units"))
        units_to_sell = round(units * sell_pct / 100.0, 6) if units else None
        alert = _alert(
            rule,
            f"{symbol} a franchi le palier {i + 1} ({_fmt_price(target)}) : vendre {sell_pct:g} %",
            {
                "level_index": i,
                "level": i + 1,
                "target_price": round(target, 6),
                "price": price,
                "pru": pru,
                "sell_pct": sell_pct,
                "units": units,
                "units_to_sell": units_to_sell,
                "gain_pct": round((price / pru - 1.0) * 100.0, 4) if pru else None,
            },
        )
    state["levels_fired"] = fired
    state["condition_active"] = any(fired)
    if alert is not None:
        state["last_fired_at"] = _now(context).isoformat()
    return alert, state


# ---------------------------------------------------------------------- allocation_drift

def _eval_allocation_drift(rule: dict[str, Any], context: dict[str, Any]) -> tuple[dict | None, dict]:
    params = rule.get("params") or {}
    state = _base_state(rule)
    category = str(params.get("category") or "")
    target = _float(params.get("target_pct"))
    threshold = _float(params.get("threshold_points"))
    min_usd = _float(params.get("min_rebalance_usd"))
    if min_usd is None:
        min_usd = 0.0
    total = _float(context.get("total_usd")) or 0.0
    alloc = (context.get("allocation") or {}).get(category) or {}

    if target is None or threshold is None or total <= 0:
        state["condition_active"] = False
        return None, state

    value = _float(alloc.get("value_usd")) or 0.0
    actual = _float(alloc.get("actual_pct"))
    if actual is None:
        actual = value / total * 100.0
    delta = actual - target
    amount = abs(delta) / 100.0 * total
    active = abs(delta) >= threshold and amount >= min_usd

    was_active = bool(state.get("condition_active"))
    state["condition_active"] = active
    state["actual_pct"] = round(actual, 4)
    state["delta_points"] = round(delta, 4)
    if not active or was_active:
        return None, state

    direction = "alléger" if delta > 0 else "renforcer"
    alert = _alert(
        rule,
        f"Allocation {category} à {actual:.1f} % (cible {target:g} %, {delta:+.1f} pts) : "
        f"{direction} d'environ {_fmt_price(amount)} USD",
        {
            "category": category,
            "actual_pct": round(actual, 4),
            "target_pct": target,
            "delta_points": round(delta, 4),
            "threshold_points": threshold,
            "rebalance_usd": round(amount, 2),
            "min_rebalance_usd": min_usd,
            "total_usd": total,
            "value_usd": value,
            "direction": direction,
        },
    )
    state["last_fired_at"] = _now(context).isoformat()
    return alert, state


# ---------------------------------------------------------------------- sentiment_zone

def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def zone_streak(fng_history: list[dict[str, Any]] | None, zone: str) -> int:
    """Nombre de jours consécutifs (les plus récents) dont la valeur tombe dans `zone`."""
    by_date: dict[date, str | None] = {}
    for entry in fng_history or []:
        d = _parse_date(entry.get("date"))
        if d is None:
            continue
        z = classify_fng(entry.get("value"))
        if z is None:
            z = zone_from_label(entry.get("classification"))
        by_date[d] = z  # la dernière entrée d'une même date l'emporte
    streak = 0
    prev: date | None = None
    for d in sorted(by_date, reverse=True):
        if prev is not None and (prev - d).days != 1:
            break  # trou dans l'historique : la série n'est plus « consécutive »
        if by_date[d] != zone:
            break
        streak += 1
        prev = d
    return streak


def _eval_sentiment_zone(rule: dict[str, Any], context: dict[str, Any]) -> tuple[dict | None, dict]:
    params = rule.get("params") or {}
    state = _base_state(rule)
    zone = str(params.get("zone") or "")
    try:
        needed = max(1, int(params.get("consecutive_days") or 3))
    except (TypeError, ValueError):
        needed = 3
    history = list(context.get("fng_history") or [])
    streak = zone_streak(history, zone)
    active = streak >= needed

    was_active = bool(state.get("condition_active"))
    state["condition_active"] = active
    state["consecutive_days"] = streak
    if not active or was_active:
        return None, state

    latest = None
    dated = [(d, e) for e in history if (d := _parse_date(e.get("date"))) is not None]
    if dated:
        latest = max(dated, key=lambda t: t[0])[1]
    alert = _alert(
        rule,
        f"Fear & Greed en zone « {zone_label(zone)} » depuis {streak} jour(s) consécutif(s)"
        + (f" (dernière valeur {int(float(latest['value']))})" if latest and _float(latest.get("value")) is not None else ""),
        {
            "zone": zone,
            "zone_label": zone_label(zone),
            "consecutive_days": streak,
            "required_days": needed,
            "latest": latest,
        },
    )
    state["last_fired_at"] = _now(context).isoformat()
    return alert, state


# ---------------------------------------------------------------------- point d'entrée

_EVALUATORS = {
    "take_profit": _eval_take_profit,
    "allocation_drift": _eval_allocation_drift,
    "sentiment_zone": _eval_sentiment_zone,
}


def evaluate(rule: dict[str, Any], context: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Évalue une règle (dict API) dans un contexte. Retourne (alerte | None, nouvel état). Pur."""
    fn = _EVALUATORS.get(str(rule.get("family") or ""))
    if fn is None:
        return None, _base_state(rule)
    return fn(rule, context or {})
