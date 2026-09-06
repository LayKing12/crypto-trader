"""
CryptoMind — Cycle de vie de l'Agent Portfolio eToro.

Démarré depuis le lifespan de main.py, à côté du bot Kraken. Inactif tant que
ETORO_API_KEY n'est pas définie : CryptoMind continue de tourner exactement comme avant.

    start()  -> câble service / store / garde-fous / notifier / journal, expose /etoro/*,
                lance run_forever() avec les signaux d'indicator_engine (etoro_signal_service)
    stop()   -> annule la tâche et ferme le client HTTP
"""
from __future__ import annotations

import asyncio

from app.utils.logging_utils import get_logger

log = get_logger("etoro_runtime")

ETORO_CYCLE_INTERVAL = 5 * 60   # même cadence que AUTO_TRADE_INTERVAL côté Kraken

_task: asyncio.Task | None = None
_agent = None
_service = None


def is_running() -> bool:
    return _task is not None and not _task.done()


async def start() -> None:
    """Idempotent. Ne lève jamais : un échec eToro ne doit pas empêcher CryptoMind de démarrer."""
    global _task, _agent, _service
    if is_running():
        return
    try:
        from etoro import api as etoro_api
        from etoro.agent import PortfolioAgent
        from etoro.config import get_settings
        from etoro.decision_log import DecisionLog
        from etoro.etoro_service import EtoroService
        from etoro.notifier import Notifier
        from etoro.risk_guard import RiskGuard
        from etoro.state_store import StateStore
        from app.services.etoro_signal_service import make_signal_provider

        settings = get_settings()
        if not settings.etoro_api_key:
            log.info("etoro_disabled", reason="ETORO_API_KEY absente")
            return

        _service = EtoroService(settings)
        store = StateStore(settings.etoro_state_path)
        store.load()
        guard = RiskGuard(settings, store)
        notifier = Notifier(settings)
        decision_log = DecisionLog(settings.etoro_decisions_path)
        decision_log.load_tail()
        _agent = PortfolioAgent(settings, _service, guard, store, notifier, decision_log=decision_log)

        # Le router /etoro/* lit ces singletons à chaque appel
        etoro_api.configure(
            store=store, guard=guard, notifier=notifier, settings=settings,
            service=_service, agent=_agent, decision_log=decision_log,
        )

        await _agent.load_universe()
        provider = make_signal_provider(_agent)
        _task = asyncio.create_task(_agent.run_forever(provider, interval_s=ETORO_CYCLE_INTERVAL))
        log.info(
            "etoro_started",
            mode=settings.etoro_mode,
            universe=settings.universe,
            max_positions=settings.etoro_max_open_positions,
            min_score=settings.etoro_min_score,
            real_mode_locked=settings.real_mode_locked,
        )
    except Exception as e:  # noqa: BLE001
        log.error("etoro_start_failed", error=str(e))


async def stop() -> None:
    global _task, _agent, _service
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
    try:
        if _agent is not None:
            await _agent.aclose()
        if _service is not None:
            await _service.aclose()
    except Exception as e:  # noqa: BLE001
        log.error("etoro_stop_error", error=str(e))
    _agent = None
    _service = None
    log.info("etoro_stopped")
