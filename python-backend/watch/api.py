"""Router FastAPI de la veille : lecture seule (observations + status).

Dépendances par injection (même schéma que `etoro/api.py`) :

    from watch import api
    api.configure(obs_log=obs_log, market=market_watch, news=news_watch, settings=settings)

Intégration dans CryptoMind :

    from watch.api import router as watch_router
    app.include_router(watch_router)   # GET /watch/observations, GET /watch/status
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from .config import WatchSettings, get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/watch", tags=["watch"])

MAX_LIMIT = 1000


class _Deps:
    """Singletons injectés par configure(). Lus à chaque appel, jamais à l'import."""

    obs_log: Any = None
    market: Any = None
    news: Any = None
    settings: WatchSettings | None = None
    started_at: datetime | None = None


_deps = _Deps()


def configure(obs_log: Any, market: Any = None, news: Any = None,
              settings: WatchSettings | None = None) -> None:
    _deps.obs_log = obs_log
    _deps.market = market
    _deps.news = news
    _deps.settings = settings
    _deps.started_at = datetime.now(timezone.utc) if obs_log is not None else None


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
    }
