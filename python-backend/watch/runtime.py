"""Cycle de vie de la veille : démarré depuis le lifespan de main.py.

    start()  -> no-op si WATCH_ENABLED=false ; sinon câble ObservationLog + MarketWatch +
                NewsWatch, expose /watch/*, lance deux tâches asyncio (marché, news)
    stop()   -> annule les tâches, ferme le client HTTP

Jamais d'exception vers main.py : un échec de la veille ne doit pas empêcher CryptoMind de
démarrer. `market_data_service` (ccxt / redis) est importé tardivement dans `start()`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import api as watch_api
from .config import WatchSettings, get_settings
from .market_watch_service import KrakenPriceFn, MarketWatch
from .news_watch_service import NewsWatch
from .observation_log import ObservationLog, new_observation

log = logging.getLogger(__name__)

_market_task: asyncio.Task | None = None
_news_task: asyncio.Task | None = None
_client: httpx.AsyncClient | None = None
_obs_log: ObservationLog | None = None
_market: MarketWatch | None = None
_news: NewsWatch | None = None


def is_running() -> bool:
    return any(t is not None and not t.done() for t in (_market_task, _news_task))


def _kraken_price_fn() -> KrakenPriceFn | None:
    """Import tardif : market_data_service tire ccxt / redis, absents dans certains environnements."""
    try:
        from app.services import market_data_service

        return market_data_service.get_price
    except Exception as exc:  # noqa: BLE001
        log.warning("watch : market_data_service indisponible, veille Kraken désactivée (%s)", exc)
        return None


def _etoro_service() -> Any:
    """Réutilise l'EtoroService déjà câblé par etoro_runtime (lecture seule : instruments + cours)."""
    try:
        from etoro import api as etoro_api

        return getattr(etoro_api._deps, "service", None)
    except Exception as exc:  # noqa: BLE001
        log.info("watch : service eToro indisponible (%s)", exc)
        return None


async def start(settings: WatchSettings | None = None) -> None:
    """Idempotent. No-op si WATCH_ENABLED=false. Ne lève jamais."""
    global _market_task, _news_task, _client, _obs_log, _market, _news
    if is_running():
        return
    try:
        settings = settings or get_settings()
        if not settings.watch_enabled:
            log.info("watch : désactivée (WATCH_ENABLED=false)")
            return

        _obs_log = ObservationLog(settings.watch_log_path, settings.watch_max_memory)
        _obs_log.load_tail()

        kraken_fn = _kraken_price_fn()
        etoro_service = _etoro_service()
        _market = MarketWatch(settings, _obs_log, kraken_price_fn=kraken_fn, etoro_service=etoro_service)

        _client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        _news = NewsWatch(settings, _obs_log, _client)

        watch_api.configure(obs_log=_obs_log, market=_market, news=_news, settings=settings)

        _obs_log.record(new_observation(
            "watch_started", "system", title="Veille démarrée",
            detail={
                "kraken_pairs": settings.kraken_pairs if kraken_fn else [],
                "etoro_symbols": settings.etoro_symbols if etoro_service is not None else [],
                "news_sources": _news.sources,
                "interval_s": settings.watch_interval_s,
                "news_interval_s": settings.watch_news_interval_s,
                "move_threshold_pct": settings.watch_move_threshold_pct,
            },
        ))
        _market_task = asyncio.create_task(_market.run_forever(settings.watch_interval_s), name="watch-market")
        _news_task = asyncio.create_task(_news.run_forever(settings.watch_news_interval_s), name="watch-news")
        log.info(
            "watch : démarrée (kraken=%s, etoro=%s, news=%s, seuil=%.2f %%)",
            bool(kraken_fn), etoro_service is not None, _news.sources, settings.watch_move_threshold_pct,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("watch : démarrage impossible (%s)", exc)
        await _cleanup()


async def _cancel(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


async def _cleanup() -> None:
    global _market_task, _news_task, _client, _obs_log, _market, _news
    await _cancel(_market_task)
    await _cancel(_news_task)
    _market_task = None
    _news_task = None
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:  # noqa: BLE001
            log.warning("watch : fermeture client HTTP (%s)", exc)
    _client = None
    _obs_log = None
    _market = None
    _news = None
    watch_api.reset()


async def stop() -> None:
    """Idempotent. Ne lève jamais."""
    was_running = is_running()
    try:
        await _cleanup()
    except Exception as exc:  # noqa: BLE001
        log.error("watch : arrêt en erreur (%s)", exc)
    if was_running:
        log.info("watch : arrêtée")
