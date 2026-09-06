"""Cycle de vie de la veille : démarré depuis le lifespan de main.py.

    start()  -> no-op si WATCH_ENABLED=false ; sinon câble ObservationLog + MarketWatch +
                NewsWatch, expose /watch/*, lance les tâches asyncio (marché, news, règles)
    stop()   -> annule les tâches, ferme le client HTTP

Règles (PR 4) : toutes les `WATCH_RULES_INTERVAL_S` (défaut 300 s), un tick construit le
contexte (prix = références du MarketWatch, snapshot portefeuille, Fear & Greed), évalue chaque
règle active via `rules.engine.evaluate`, persiste l'état et, sur transition, crée l'alerte puis
la pousse sur Telegram (`notifications.telegram_service.send_watch_alert`, import tardif, no-op
si absent). Les alertes sont à valider manuellement : aucun ordre n'est passé ici.

Jamais d'exception vers main.py : un échec de la veille ne doit pas empêcher CryptoMind de
démarrer. `market_data_service` (ccxt / redis) et `app.database` sont importés tardivement.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

import httpx

from . import api as watch_api
from . import fng_source, portfolio
from .config import WatchSettings, get_settings
from .market_watch_service import KrakenPriceFn, MarketWatch
from .news_watch_service import NewsWatch
from .observation_log import ObservationLog, new_observation
from .rules import engine as rules_engine
from .rules import store as rules_store_mod
from .rules.models import WatchBase
from .rules.store import RulesStore

log = logging.getLogger(__name__)

_market_task: asyncio.Task | None = None
_news_task: asyncio.Task | None = None
_rules_task: asyncio.Task | None = None
_client: httpx.AsyncClient | None = None
_obs_log: ObservationLog | None = None
_market: MarketWatch | None = None
_news: NewsWatch | None = None
_settings: WatchSettings | None = None
_store: RulesStore | None = None
_rules_engine_obj: Any = None          # engine SQLAlchemy créé ici (WATCH_DATABASE_URL) ; None si app.database
_etoro: Any = None
_kraken_balance: Any = None
rules_tick_count = 0
rules_last_error: str | None = None


def is_running() -> bool:
    return any(t is not None and not t.done() for t in (_market_task, _news_task, _rules_task))


def rules_enabled() -> bool:
    return _store is not None


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


def _kraken_balance_fn() -> Any:
    """Soldes Kraken valorisés en USD via ccxt (import tardif). None si indisponible."""
    try:
        from app.services import execution_service
    except Exception as exc:  # noqa: BLE001
        log.info("watch : execution_service indisponible, crypto Kraken absent du portefeuille (%s)", exc)
        return None

    async def balances() -> dict[str, float]:
        exchange = execution_service._get_exchange()
        raw = await exchange.fetch_balance()
        totals = raw.get("total") if isinstance(raw, dict) else None
        if not isinstance(totals, dict):
            return {}
        refs = _market.references() if _market is not None else {}
        out: dict[str, float] = {}
        for asset, qty in totals.items():
            try:
                q = float(qty or 0.0)
            except (TypeError, ValueError):
                continue
            if q <= 0:
                continue
            base = str(asset).upper().lstrip("XZ") if len(str(asset)) == 4 and str(asset)[0] in "XZ" else str(asset).upper()
            if base in ("USD", "USDT", "USDC", "ZUSD"):
                out[f"{base}"] = out.get(base, 0.0) + q
                continue
            price = refs.get(f"kraken:{base}USD")
            if price is None:
                try:
                    from app.services import market_data_service

                    price = await market_data_service.get_price(f"{base}USD")
                except Exception:  # noqa: BLE001
                    price = None
            if price:
                out[f"{base}USD"] = out.get(f"{base}USD", 0.0) + q * float(price)
        return out

    return balances


# ---------------------------------------------------------------------- règles : base

async def _setup_rules(settings: WatchSettings) -> RulesStore | None:
    """Crée les tables WatchBase et le store. Retourne None (règles désactivées) si aucune base."""
    global _rules_engine_obj
    engine = None
    if settings.watch_database_url:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(settings.watch_database_url)
            _rules_engine_obj = engine
        except Exception as exc:  # noqa: BLE001
            log.error("watch rules : WATCH_DATABASE_URL inutilisable (%s) : règles désactivées", exc)
            return None
    else:
        try:
            from app import database as app_db

            engine = getattr(app_db, "engine", None)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : app.database indisponible (%s) : règles désactivées "
                        "(DATABASE_URL absent ?)", exc)
            return None
    if engine is None:
        log.warning("watch rules : aucun engine de base de données : règles désactivées")
        return None
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async with engine.begin() as conn:
            await conn.run_sync(WatchBase.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    except Exception as exc:  # noqa: BLE001
        log.error("watch rules : création des tables impossible (%s) : règles désactivées", exc)
        return None
    store = RulesStore(session_factory)
    rules_store_mod.configure(session_factory)
    return store


# ---------------------------------------------------------------------- règles : contexte

def _prices_from_market() -> dict[str, float]:
    """Références du MarketWatch : "etoro:AAPL" -> {"AAPL": px, "etoro:AAPL": px}."""
    prices: dict[str, float] = {}
    if _market is None:
        return prices
    try:
        refs = _market.references()
    except Exception:  # noqa: BLE001
        return prices
    for key, price in refs.items():
        prices[key] = price
        _, _, symbol = key.partition(":")
        if symbol:
            prices.setdefault(symbol.upper(), price)
    return prices


async def _targets_from_rules(store: RulesStore) -> dict[str, float]:
    targets: dict[str, float] = {}
    for rule in await store.list_rules(enabled=True, family="allocation_drift"):
        params = rule.get("params") or {}
        cat = params.get("category")
        if cat and params.get("target_pct") is not None:
            targets[str(cat)] = float(params["target_pct"])
    return targets


async def collect_portfolio() -> dict[str, Any]:
    """Snapshot portefeuille temps réel (utilisé par GET /watch/allocation et par le tick règles)."""
    targets: dict[str, float] = {}
    if _store is not None:
        try:
            targets = await _targets_from_rules(_store)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : cibles d'allocation illisibles (%s)", exc)
    symbols = _settings.etoro_symbols if _settings is not None else None
    return await portfolio.collect_snapshot(_etoro, _kraken_balance, targets=targets, symbols=symbols)


async def fetch_fng_history() -> list[dict[str, Any]]:
    if _settings is not None and not _settings.watch_fng_enabled:
        return []
    return await fng_source.fetch_fng(_client, limit=30)


async def _send_telegram(alert: dict[str, Any]) -> int | None:
    """`notifications.telegram_service.send_watch_alert` en import tardif ; no-op si absent."""
    try:
        from notifications import telegram_service  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - module absent (écrit par un autre agent) : no-op
        return None
    try:
        fn = getattr(telegram_service, "send_watch_alert", None)
        if fn is None:
            return None
        result = fn(alert)
        if inspect.isawaitable(result):
            result = await result
        return int(result) if result is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("watch rules : envoi Telegram impossible (%s)", exc)
        return None


async def evaluate_rules(store: RulesStore, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Évalue chaque règle active, persiste l'état, crée les alertes (+ Telegram) sur transition."""
    created: list[dict[str, Any]] = []
    for rule in await store.list_rules(enabled=True):
        try:
            alert, new_state = rules_engine.evaluate(rule, context)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : évaluation de %s (%s) en erreur : %s", rule.get("name"), rule.get("id"), exc)
            continue
        try:
            if new_state != (rule.get("state") or {}):
                await store.set_rule_state(rule["id"], new_state)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : état de %s non persisté (%s)", rule.get("id"), exc)
        if alert is None:
            continue
        try:
            saved = await store.create_alert(alert)
        except Exception as exc:  # noqa: BLE001
            log.error("watch rules : alerte non persistée pour %s (%s)", rule.get("id"), exc)
            continue
        message_id = await _send_telegram(saved)
        if message_id is not None:
            try:
                await store.set_alert_telegram_id(saved["id"], message_id)
                saved["telegram_message_id"] = message_id
            except Exception as exc:  # noqa: BLE001
                log.warning("watch rules : telegram_message_id non persisté (%s)", exc)
        created.append(saved)
        log.info("watch rules : alerte « %s » (règle %s)", saved.get("title"), rule.get("name"))
    return created


async def rules_tick() -> list[dict[str, Any]]:
    """Un cycle règles complet. Ne lève jamais."""
    global rules_tick_count, rules_last_error
    if _store is None:
        return []
    created: list[dict[str, Any]] = []
    try:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = await collect_portfolio()
            await _store.add_snapshot(snapshot)  # 1 par heure max, géré par le store
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : snapshot portefeuille impossible (%s)", exc)
        fng_history: list[dict[str, Any]] = []
        try:
            fng_history = await fetch_fng_history()
            if fng_history:
                await _store.upsert_daily(fng_history)
            else:
                fng_history = await _store.fng_history(30)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : Fear & Greed indisponible (%s)", exc)
        context = {
            "prices": _prices_from_market(),
            "positions": snapshot.get("positions") or {},
            "allocation": snapshot.get("allocation") or {},
            "total_usd": float(snapshot.get("total_usd") or 0.0),
            "fng_history": fng_history,
            "now": None,
        }
        created = await evaluate_rules(_store, context)
        rules_last_error = None
    except Exception as exc:  # noqa: BLE001
        rules_last_error = f"{type(exc).__name__}: {exc}"
        log.exception("watch rules : tick en erreur")
    rules_tick_count += 1
    return created


async def _rules_loop(interval_s: float) -> None:
    log.info("watch rules : démarrage (intervalle %.0fs)", interval_s)
    # Laisse le premier tick marché poser ses références de prix avant la première évaluation.
    await asyncio.sleep(min(30.0, interval_s))
    while True:
        try:
            await rules_tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - rules_tick() ne lève pas, ceinture et bretelles
            log.exception("watch rules : boucle en erreur")
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------- cycle de vie

async def start(settings: WatchSettings | None = None) -> None:
    """Idempotent. No-op si WATCH_ENABLED=false. Ne lève jamais."""
    global _market_task, _news_task, _rules_task, _client, _obs_log, _market, _news
    global _settings, _store, _etoro, _kraken_balance
    if is_running():
        return
    try:
        settings = settings or get_settings()
        _settings = settings
        if not settings.watch_enabled:
            log.info("watch : désactivée (WATCH_ENABLED=false)")
            return

        _obs_log = ObservationLog(settings.watch_log_path, settings.watch_max_memory)
        _obs_log.load_tail()

        kraken_fn = _kraken_price_fn()
        _etoro = _etoro_service()
        _market = MarketWatch(settings, _obs_log, kraken_price_fn=kraken_fn, etoro_service=_etoro)

        _client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        _news = NewsWatch(settings, _obs_log, _client)

        try:
            _store = await _setup_rules(settings)
        except Exception as exc:  # noqa: BLE001
            log.error("watch rules : initialisation impossible (%s) : règles désactivées", exc)
            _store = None
        _kraken_balance = _kraken_balance_fn() if kraken_fn is not None else None

        watch_api.configure(
            obs_log=_obs_log, market=_market, news=_news, settings=settings,
            rules_store=_store,
            portfolio_fn=collect_portfolio if _store is not None else None,
            fng_fn=fetch_fng_history if settings.watch_fng_enabled else None,
        )

        _obs_log.record(new_observation(
            "watch_started", "system", title="Veille démarrée",
            detail={
                "kraken_pairs": settings.kraken_pairs if kraken_fn else [],
                "etoro_symbols": settings.etoro_symbols if _etoro is not None else [],
                "news_sources": _news.sources,
                "interval_s": settings.watch_interval_s,
                "news_interval_s": settings.watch_news_interval_s,
                "move_threshold_pct": settings.watch_move_threshold_pct,
                "rules": _store is not None,
                "rules_interval_s": settings.watch_rules_interval_s,
            },
        ))
        _market_task = asyncio.create_task(_market.run_forever(settings.watch_interval_s), name="watch-market")
        _news_task = asyncio.create_task(_news.run_forever(settings.watch_news_interval_s), name="watch-news")
        if _store is not None:
            _rules_task = asyncio.create_task(_rules_loop(settings.watch_rules_interval_s), name="watch-rules")
        log.info(
            "watch : démarrée (kraken=%s, etoro=%s, news=%s, seuil=%.2f %%, règles=%s)",
            bool(kraken_fn), _etoro is not None, _news.sources, settings.watch_move_threshold_pct,
            _store is not None,
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
    global _market_task, _news_task, _rules_task, _client, _obs_log, _market, _news
    global _store, _rules_engine_obj, _etoro, _kraken_balance
    await _cancel(_market_task)
    await _cancel(_news_task)
    await _cancel(_rules_task)
    _market_task = None
    _news_task = None
    _rules_task = None
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:  # noqa: BLE001
            log.warning("watch : fermeture client HTTP (%s)", exc)
    _client = None
    if _rules_engine_obj is not None:
        try:
            await _rules_engine_obj.dispose()
        except Exception as exc:  # noqa: BLE001
            log.warning("watch rules : fermeture engine (%s)", exc)
    _rules_engine_obj = None
    _store = None
    rules_store_mod.configure(None)
    _etoro = None
    _kraken_balance = None
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
