"""Router FastAPI de la veille : observations + status (PR 3), alertes / règles /
portefeuille / Fear & Greed (PR 4, voir CONTRACT_PR4.md).

Dépendances par injection (même schéma que `etoro/api.py`) :

    from watch import api
    api.configure(obs_log=obs_log, market=market_watch, news=news_watch, settings=settings,
                  rules_store=store, portfolio_fn=collect, fng_fn=fetch)

    rules_store  : watch.rules.store.RulesStore (None = règles indisponibles -> 503)
    portfolio_fn : callable async () -> snapshot (voir watch/portfolio.py)
    fng_fn       : callable async () -> [{date, value, classification}] (plus récent en premier)

Intégration dans CryptoMind :

    from watch.api import router as watch_router
    app.include_router(watch_router)

Les règles produisent des alertes à valider manuellement : aucune route n'exécute d'ordre.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from .config import WatchSettings, get_settings
from .rules import schemas
from .rules.store import AlertAlreadyHandled, AlertNotFound, RuleNotFound

log = logging.getLogger(__name__)

router = APIRouter(prefix="/watch", tags=["watch"])

MAX_LIMIT = 1000
HISTORY_DAYS_CHOICES = (7, 30, 90, 365)


class _Deps:
    """Singletons injectés par configure(). Lus à chaque appel, jamais à l'import."""

    obs_log: Any = None
    market: Any = None
    news: Any = None
    settings: WatchSettings | None = None
    started_at: datetime | None = None
    rules_store: Any = None
    portfolio_fn: Any = None
    fng_fn: Any = None


_deps = _Deps()


def configure(obs_log: Any, market: Any = None, news: Any = None,
              settings: WatchSettings | None = None, rules_store: Any = None,
              portfolio_fn: Any = None, fng_fn: Any = None) -> None:
    _deps.obs_log = obs_log
    _deps.market = market
    _deps.news = news
    _deps.settings = settings
    _deps.started_at = datetime.now(timezone.utc) if obs_log is not None else None
    _deps.rules_store = rules_store
    _deps.portfolio_fn = portfolio_fn
    _deps.fng_fn = fng_fn


def reset() -> None:
    """Retire toutes les dépendances (arrêt du runtime, tests)."""
    configure(None)


def _settings() -> WatchSettings:
    return _deps.settings or get_settings()


@router.get("/observations")
async def observations(limit: int = 100, kind: str | None = None, symbol: str | None = None,
                       source: str | None = None) -> dict[str, Any]:
    """Observations les plus récentes en premier, filtrables par kind / symbol / source."""
    if _deps.obs_log is None:
        return {"observations": [], "count": 0, "available": False}
    limit = max(1, min(MAX_LIMIT, int(limit)))
    try:
        records = _deps.obs_log.recent(
            limit=limit, kind=kind or None, symbol=symbol or None, source=source or None,
        )
    except Exception:  # noqa: BLE001
        log.exception("GET /watch/observations : lecture du journal impossible")
        return {"observations": [], "count": 0, "available": False}
    out = [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in records]
    return {"observations": out, "count": len(out), "available": True}


@router.get("/status")
async def status() -> dict[str, Any]:
    """État de la veille : activée ?, derniers ticks, volume d'observations, seuils."""
    settings = _settings()
    market = _deps.market
    news = _deps.news
    return {
        "enabled": settings.watch_enabled,
        "running": _deps.obs_log is not None,
        "started_at": _deps.started_at.isoformat() if _deps.started_at else None,
        "observations": len(_deps.obs_log) if _deps.obs_log is not None else 0,
        "log_path": str(getattr(_deps.obs_log, "path", None)) if _deps.obs_log is not None
        and getattr(_deps.obs_log, "path", None) else None,
        "market": market.status() if market is not None else None,
        "news": news.status() if news is not None else None,
        "market_last_tick_at": market.last_tick_at.isoformat() if market is not None and market.last_tick_at else None,
        "news_last_tick_at": news.last_tick_at.isoformat() if news is not None and news.last_tick_at else None,
        "thresholds": {
            "move_threshold_pct": settings.watch_move_threshold_pct,
            "interval_s": settings.watch_interval_s,
            "news_interval_s": settings.watch_news_interval_s,
            "max_memory": settings.watch_max_memory,
        },
        "config": {
            "kraken_pairs": settings.kraken_pairs,
            "etoro_symbols": settings.etoro_symbols,
            "news_sources": settings.news_sources,
            "cryptopanic": settings.cryptopanic_enabled,
        },
        "time": datetime.now(timezone.utc).isoformat(),
        "rules": {
            "available": _deps.rules_store is not None,
            "portfolio": _deps.portfolio_fn is not None,
            "fng": _deps.fng_fn is not None,
        },
    }


# ============================================================================ PR 4
# Alertes / règles / portefeuille / Fear & Greed. Aucune route ne déclenche d'ordre.

def _store() -> Any:
    if _deps.rules_store is None:
        raise HTTPException(status_code=503, detail="règles de veille indisponibles (base non configurée)")
    return _deps.rules_store


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Appelle un callable sync ou async injecté."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


# ------------------------------------------------------------------ alertes
@router.get("/alerts")
async def alerts(status: str = "pending", limit: int = 50) -> dict[str, Any]:
    """Plus récent en premier. `status=all` pour tout ; les `postponed` expirés redeviennent `pending`."""
    if _deps.rules_store is None:
        return {"alerts": [], "count": 0, "available": False}
    limit = max(1, min(MAX_LIMIT, int(limit)))
    try:
        rows = await _deps.rules_store.list_alerts(status=status or "pending", limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001
        log.exception("GET /watch/alerts : lecture impossible")
        return {"alerts": [], "count": 0, "available": False}
    return {"alerts": rows, "count": len(rows), "available": True}


@router.post("/alerts/{alert_id}/action")
async def alert_action(alert_id: str, body: schemas.AlertActionBody) -> dict[str, Any]:
    """executed | ignored | postponed (postpone_hours, défaut 24). 404 inconnue, 409 déjà traitée."""
    store = _store()
    try:
        return await store.apply_alert_action(
            alert_id, body.action, actor="ui", postpone_hours=body.postpone_hours,
        )
    except AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=f"alerte inconnue : {alert_id}") from exc
    except AlertAlreadyHandled as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "alert": exc.alert}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------------------ règles
@router.get("/rules")
async def rules() -> dict[str, Any]:
    if _deps.rules_store is None:
        return {"rules": [], "available": False}
    try:
        rows = await _deps.rules_store.list_rules()
    except Exception:  # noqa: BLE001
        log.exception("GET /watch/rules : lecture impossible")
        return {"rules": [], "available": False}
    return {"rules": rows, "available": True}


@router.post("/rules", status_code=201)
async def create_rule(body: schemas.RuleCreate) -> dict[str, Any]:
    """422 (pydantic) si les params ne correspondent pas à la famille."""
    store = _store()
    try:
        return await store.create_rule(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, body: schemas.RuleUpdate) -> dict[str, Any]:
    """Body partiel ; les params sont validés après fusion avec la règle existante."""
    store = _store()
    try:
        return await store.update_rule(rule_id, body.model_dump(exclude_unset=True))
    except RuleNotFound as exc:
        raise HTTPException(status_code=404, detail=f"règle inconnue : {rule_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str) -> dict[str, Any]:
    store = _store()
    if not await store.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"règle inconnue : {rule_id}")
    return {"deleted": True}


# ------------------------------------------------------------------ portefeuille
@router.get("/portfolio/history")
async def portfolio_history(days: int = 30) -> dict[str, Any]:
    """Points {at, value_usd} du plus ancien au plus récent. days ∈ 7/30/90/365 (borné à 1..365)."""
    days = max(1, min(365, int(days)))
    if _deps.rules_store is None:
        return {"points": [], "days": days, "available": False}
    try:
        points = await _deps.rules_store.history(days)
    except Exception:  # noqa: BLE001
        log.exception("GET /watch/portfolio/history : lecture impossible")
        return {"points": [], "days": days, "available": False}
    return {"points": points, "days": days, "available": True}


@router.get("/allocation")
async def allocation() -> dict[str, Any]:
    """Allocation réelle vs cible. Source : portfolio_fn (temps réel), sinon dernier snapshot."""
    snapshot: dict[str, Any] | None = None
    if _deps.portfolio_fn is not None:
        try:
            snapshot = await _call(_deps.portfolio_fn)
        except Exception:  # noqa: BLE001
            log.exception("GET /watch/allocation : collecte impossible")
            snapshot = None
    if snapshot is None and _deps.rules_store is not None:
        try:
            snapshot = await _deps.rules_store.latest_snapshot()
        except Exception:  # noqa: BLE001
            log.exception("GET /watch/allocation : dernier snapshot illisible")
    if not snapshot:
        return {"total_usd": 0.0, "categories": [], "at": None, "available": False}
    return {
        "total_usd": float(snapshot.get("total_usd") or 0.0),
        "categories": list(snapshot.get("categories") or []),
        "at": snapshot.get("at"),
        "available": True,
    }


# ------------------------------------------------------------------ Fear & Greed
@router.get("/fng")
async def fng(limit: int = 30) -> dict[str, Any]:
    """Valeur du jour + historique (plus récent en premier). Source : fng_fn, sinon table watch_fng_daily."""
    limit = max(1, min(365, int(limit)))
    history: list[dict[str, Any]] = []
    if _deps.fng_fn is not None:
        try:
            history = list(await _call(_deps.fng_fn) or [])
        except Exception:  # noqa: BLE001
            log.exception("GET /watch/fng : source indisponible")
            history = []
    if not history and _deps.rules_store is not None:
        try:
            history = await _deps.rules_store.fng_history(limit)
        except Exception:  # noqa: BLE001
            log.exception("GET /watch/fng : historique illisible")
            history = []
    history = sorted(history, key=lambda e: str(e.get("date") or ""), reverse=True)[:limit]
    latest = history[0] if history else None
    return {
        "value": latest.get("value") if latest else None,
        "classification": latest.get("classification") if latest else None,
        "date": latest.get("date") if latest else None,
        "history": history,
        "available": bool(history),
    }
