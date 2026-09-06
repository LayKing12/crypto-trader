"""Router FastAPI du module eToro : kill switch, status, health.

Dépendances par injection (aucun import de state_store / risk_guard / agent au niveau module) :

    from etoro import api
    api.configure(store=store, guard=guard, notifier=notifier, settings=settings)

Intégration dans CryptoMind (app FastAPI existante) :

    from etoro.api import router
    app.include_router(router)          # expose /etoro/kill, /etoro/resume, /etoro/status, /etoro/decisions, /etoro/health

Déploiement autonome (Railway) :

    uvicorn etoro.api:app --host 0.0.0.0 --port $PORT     # `app = create_app()` en bas de ce fichier
"""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException

from .config import Settings, get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/etoro", tags=["etoro"])


class _Deps:
    """Singletons injectés par configure(). Lus à chaque appel, jamais à l'import."""

    store: Any = None
    guard: Any = None
    notifier: Any = None
    settings: Settings | None = None
    service: Any = None   # EtoroService (optionnel) : alimente /positions
    agent: Any = None     # PortfolioAgent (optionnel) : mapping instrument_id -> symbole
    decision_log: Any = None  # DecisionLog (optionnel) : alimente /decisions


_deps = _Deps()


def configure(
    store: Any,
    guard: Any = None,
    notifier: Any = None,
    settings: Settings | None = None,
    service: Any = None,
    agent: Any = None,
    decision_log: Any = None,
) -> None:
    """Pose les dépendances utilisées par le router (store obligatoire, le reste optionnel)."""
    _deps.store = store
    _deps.guard = guard
    _deps.notifier = notifier
    _deps.settings = settings
    _deps.service = service
    _deps.agent = agent
    _deps.decision_log = decision_log


def _settings() -> Settings:
    return _deps.settings or get_settings()


def _store() -> Any:
    if _deps.store is None:
        raise HTTPException(status_code=503, detail="store non configuré (appeler etoro.api.configure)")
    return _deps.store


def _check_token(token: str | None) -> None:
    expected = _settings().etoro_kill_switch_token
    if not expected:
        raise HTTPException(status_code=503, detail="ETORO_KILL_SWITCH_TOKEN non configuré")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="X-Kill-Token invalide")


def _breaker_until() -> datetime | None:
    value = getattr(_deps.store, "breaker_until", None)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


def _breaker_active() -> bool:
    if _deps.guard is not None and hasattr(_deps.guard, "is_breaker_active"):
        return bool(_deps.guard.is_breaker_active())
    until = _breaker_until()
    if until is None:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _kill_switch_active() -> bool:
    if _deps.guard is not None and hasattr(_deps.guard, "kill_switch_active"):
        return bool(_deps.guard.kill_switch_active())
    return bool(getattr(_deps.store, "kill_switch", False)) or not _settings().etoro_agent_enabled


async def _notify_kill_switch(actor: str, enabled: bool) -> None:
    if _deps.notifier is None:
        return
    try:
        await _deps.notifier.send_kill_switch(actor, enabled)
    except Exception:  # noqa: BLE001 - la notif ne doit jamais bloquer le kill switch
        log.exception("Notification kill switch impossible")


async def _set_kill_switch(enabled: bool, token: str | None, actor: str) -> dict[str, Any]:
    _check_token(token)
    store = _store()
    store.set_kill_switch(enabled, actor)
    log.warning("Kill switch %s par %s", "ACTIVÉ" if enabled else "désactivé", actor)
    await _notify_kill_switch(actor, enabled)
    return {"kill_switch": enabled, "actor": actor, "mode": _settings().etoro_mode}


@router.post("/kill")
async def kill(
    x_kill_token: str | None = Header(default=None, alias="X-Kill-Token"),
    x_kill_actor: str = Header(default="api", alias="X-Kill-Actor"),
) -> dict[str, Any]:
    """Active le kill switch : bloque toute nouvelle ouverture (ne ferme pas les positions)."""
    return await _set_kill_switch(True, x_kill_token, x_kill_actor)


@router.post("/resume")
async def resume(
    x_kill_token: str | None = Header(default=None, alias="X-Kill-Token"),
    x_kill_actor: str = Header(default="api", alias="X-Kill-Actor"),
) -> dict[str, Any]:
    """Désactive le kill switch (ETORO_AGENT_ENABLED=false reste prioritaire)."""
    return await _set_kill_switch(False, x_kill_token, x_kill_actor)


@router.get("/status")
async def status() -> dict[str, Any]:
    """État courant : mode, kill switch, breaker et snapshot du store."""
    store = _store()
    until = _breaker_until()
    settings = _settings()
    return {
        "mode": settings.etoro_mode,
        "agent_enabled": settings.etoro_agent_enabled,
        "real_mode_locked": settings.real_mode_locked,
        "kill_switch": bool(getattr(store, "kill_switch", False)),
        "kill_switch_active": _kill_switch_active(),
        "kill_switch_actor": getattr(store, "kill_switch_actor", None),
        "breaker_active": _breaker_active(),
        "breaker_until": until.isoformat() if until else None,
        "max_open_positions": settings.etoro_max_open_positions,
        "universe": settings.universe,
        "snapshot": store.snapshot(),
    }


@router.get("/positions")
async def positions() -> dict[str, Any]:
    """Positions ouvertes chez eToro (via EtoroService si injecté), enrichies du symbole."""
    if _deps.service is None:
        return {"available": False, "positions": [], "detail": "service eToro non injecté"}
    symbols: dict[int, str] = {}
    agent = _deps.agent
    if agent is not None:
        for sym, inst in getattr(agent, "instruments_by_symbol", {}).items():
            symbols[int(getattr(inst, "instrument_id", 0))] = sym
    try:
        raw = await _deps.service.get_open_positions()
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /etoro/positions : %s", exc)
        return {"available": False, "positions": [], "detail": "erreur API eToro"}
    out = []
    for pos in raw:
        d = pos.model_dump(mode="json") if hasattr(pos, "model_dump") else dict(pos)
        d["symbol"] = symbols.get(int(d.get("instrument_id", 0)), str(d.get("instrument_id")))
        out.append(d)
    return {"available": True, "positions": out}


@router.get("/decisions")
async def decisions(limit: int = 100, kind: str | None = None, symbol: str | None = None) -> dict[str, Any]:
    """Journal des décisions de l'agent (plus récent en premier), filtrable par kind / symbole."""
    if _deps.decision_log is None:
        return {"decisions": [], "count": 0, "available": False}
    limit = max(1, min(500, int(limit)))
    try:
        records = _deps.decision_log.recent(limit=limit, kind=kind or None, symbol=symbol or None)
    except Exception:  # noqa: BLE001
        log.exception("GET /etoro/decisions : lecture du journal impossible")
        return {"decisions": [], "count": 0, "available": False}
    out = [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in records]
    return {"decisions": out, "count": len(out), "available": True}


@router.get("/health")
async def health() -> dict[str, Any]:
    """Sonde de vie (Railway healthcheck)."""
    return {
        "status": "ok",
        "mode": _settings().etoro_mode,
        "store_configured": _deps.store is not None,
        "time": datetime.now(timezone.utc).isoformat(),
    }


def create_app(autowire: bool = True) -> FastAPI:
    """App FastAPI autonome (Railway). Si `autowire`, câble store/guard/notifier au démarrage
    quand configure() n'a pas déjà été appelé ; les modules sont importés tardivement."""

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        if autowire and _deps.store is None:
            _autowire()
        yield

    app = FastAPI(title="CryptoMind eToro", version="0.1.0", lifespan=_lifespan)
    app.include_router(router)
    return app


def _autowire() -> None:  # pragma: no cover - câblage réel, testé manuellement
    settings = _settings()
    try:
        from .notifier import Notifier
        from .risk_guard import RiskGuard
        from .state_store import StateStore

        store = StateStore(settings.etoro_state_path)
        store.load()
        from .decision_log import DecisionLog

        decision_log = DecisionLog(settings.etoro_decisions_path)
        decision_log.load_tail()
        service = None
        if settings.etoro_api_key:
            from .etoro_service import EtoroService

            service = EtoroService(settings)
        configure(
            store=store,
            guard=RiskGuard(settings, store),
            notifier=Notifier(settings),
            settings=settings,
            service=service,
            decision_log=decision_log,
        )
        log.info("etoro.api câblé automatiquement (mode %s)", settings.etoro_mode)
    except Exception:  # noqa: BLE001
        log.exception("Câblage automatique impossible : /kill, /resume, /status répondront 503")


app = create_app()
