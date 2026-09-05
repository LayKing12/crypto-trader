"""API Rankings eToro -> signal de confirmation "smart money".

Principe : on récupère le classement des investisseurs sur 12 mois, on ne garde que les
profils réguliers (drawdown borné, historique >= 12 mois, mois profitables >= seuil), on
classe les N meilleurs par un score de régularité (pas par gain brut), puis on regarde
combien d'entre eux sont exposés sur l'instrument demandé. Le ratio (exposés / inspectés)
est renvoyé dans [0, 1]. Aucune exception ne remonte à l'appelant : erreur => log + None.

Sources : voir docs/rankings.md (endpoints confirmés sur api-portal.etoro.com).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .config import Settings
from .models import Side

logger = logging.getLogger("etoro.rankings")

# --- Endpoints (chemins relatifs à settings.base_url) -------------------------------------
# Confirmés par https://api-portal.etoro.com (voir docs/rankings.md). Aucun "À VÉRIFIER" sur les
# chemins eux-mêmes ; la sémantique fine de `period` (rolling vs calendaire) reste une hypothèse.
ENDPOINTS: dict[str, str] = {
    # GET ?period=...&sort=-gain&page=1&pageSize=100  (confirmé)
    "rankings": "/api/v2/portfolios/rankings",
    # GET  -> positions publiques d'un utilisateur (confirmé)
    "user_portfolio": "/api/v1/user-info/people/{username}/portfolio/live",
    # GET ?period=...  -> ligne de classement d'un seul investisseur (confirmé, non utilisé ici)
    "user_ranking": "/api/v2/portfolios/{username}/rankings",
}

# Période 12 mois glissants. Hypothèse : "OneYearAgo" = fenêtre roulante de 12 mois
# (alternatives documentées : "LastYear" = année civile précédente, "AbsOneYear").
RANKINGS_PERIOD = "OneYearAgo"
# Tri serveur : on ratisse large (gain décroissant) puis on filtre/re-trie côté client.
RANKINGS_SORT = "-gain"
RANKINGS_PAGE_SIZE = 100  # max autorisé par l'API
RANKINGS_MAX_PAGES = 5  # 500 candidats max par rafraîchissement
MIN_HISTORY_DAYS = 365  # ">= 12 mois d'historique"
PORTFOLIO_CONCURRENCY = 5  # quota partagé : 60 req / 60 s

CACHE_TTL_S = 3600.0  # 1 h


class TraderRank(BaseModel):
    """Ligne de classement normalisée (sous-ensemble utile des champs eToro)."""

    cid: int | None = None
    username: str
    gain: float = 0.0
    daily_dd: float = 0.0  # max drawdown journalier sur la période (valeur absolue, %)
    weekly_dd: float = 0.0  # max drawdown hebdo (valeur absolue, %)
    peak_to_valley: float | None = None  # max drawdown crête-creux (valeur absolue, %)
    risk_score: int | None = None
    max_daily_risk_score: int | None = None
    profitable_months_pct: float = 0.0
    active_weeks: int | None = None
    weeks_since_registration: int | None = None
    first_activity: datetime | None = None
    copiers: int = 0
    popular_investor: bool = False
    score: float = Field(default=0.0, description="Score de régularité (voir docs/rankings.md)")

    @property
    def max_drawdown(self) -> float:
        """Drawdown max retenu pour le filtre : peakToValley si présent, sinon max(daily, weekly)."""
        if self.peak_to_valley is not None:
            return self.peak_to_valley
        return max(self.daily_dd, self.weekly_dd)

    def history_days(self, now: datetime) -> float | None:
        """Ancienneté en jours : firstActivity, sinon weeksSinceRegistration, sinon activeWeeks."""
        if self.first_activity is not None:
            return (now - self.first_activity).total_seconds() / 86400.0
        if self.weeks_since_registration is not None:
            return self.weeks_since_registration * 7.0
        if self.active_weeks is not None:
            return self.active_weeks * 7.0
        return None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TraderRank:
        """Construit depuis une ligne API (tolère camelCase v2 et PascalCase de l'ancienne API)."""
        return cls(
            cid=_as_int(_pick(raw, "cid", "customerId", "CID")),
            username=str(_pick(raw, "username", "userName", "UserName") or ""),
            gain=_as_float(_pick(raw, "gain", "Gain")) or 0.0,
            daily_dd=abs(_as_float(_pick(raw, "dailyDD", "DailyDD")) or 0.0),
            weekly_dd=abs(_as_float(_pick(raw, "weeklyDD", "WeeklyDD")) or 0.0),
            peak_to_valley=_abs_or_none(_as_float(_pick(raw, "peakToValley", "PeakToValley"))),
            risk_score=_as_int(_pick(raw, "riskScore", "RiskScore")),
            max_daily_risk_score=_as_int(_pick(raw, "maxDailyRiskScore", "MaxDailyRiskScore")),
            profitable_months_pct=_as_float(_pick(raw, "profitableMonthsPct", "ProfitableMonthsPct")) or 0.0,
            active_weeks=_as_int(_pick(raw, "activeWeeks", "ActiveWeeks")),
            weeks_since_registration=_as_int(_pick(raw, "weeksSinceRegistration", "WeeksSinceRegistration")),
            first_activity=_as_datetime(_pick(raw, "firstActivity", "FirstActivity")),
            copiers=_as_int(_pick(raw, "copiers", "Copiers")) or 0,
            popular_investor=bool(_pick(raw, "popularInvestor", "PopularInvestor", "isPopularInvestor") or False),
        )


# --- Cache mémoire simple (TTL) ---------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: float = CACHE_TTL_S) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def clear_cache() -> None:
    """Vide le cache (utile pour les tests ou un rafraîchissement forcé)."""
    _cache.clear()


# --- Helpers -----------------------------------------------------------------------------------
def _headers(settings: Settings) -> dict[str, str]:
    headers = {
        "x-api-key": settings.etoro_api_key,
        "x-request-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    if settings.etoro_user_key:
        headers["x-user-key"] = settings.etoro_user_key
    return headers


def _pick(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    lowered = {k.lower(): v for k, v in raw.items()}
    for name in names:
        v = lowered.get(name.lower())
        if v is not None:
            return v
    return None


def _as_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _abs_or_none(v: float | None) -> float | None:
    return None if v is None else abs(v)


def _as_datetime(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def regularity_score(t: TraderRank) -> float:
    """Score de régularité : gain / (1 + maxDD) pondéré par la part de mois profitables.

    Un gain de 30 % avec 5 % de DD (score ~ 5 x pm) bat un gain de 60 % avec 15 % de DD (~3.75 x pm).
    """
    return t.gain / (1.0 + t.max_drawdown) * (t.profitable_months_pct / 100.0)


def filter_traders(traders: list[TraderRank], settings: Settings, now: datetime | None = None) -> list[TraderRank]:
    """Applique les filtres du contrat : DD max, historique >= 12 mois, mois profitables >= seuil."""
    now = now or datetime.now(timezone.utc)
    kept: list[TraderRank] = []
    for t in traders:
        if not t.username:
            continue
        if t.max_drawdown > settings.rankings_max_drawdown_pct:
            continue
        days = t.history_days(now)
        if days is None or days < MIN_HISTORY_DAYS:
            continue
        if t.profitable_months_pct < settings.rankings_min_profitable_months_pct:
            continue
        kept.append(t)
    return kept


def rank_traders(traders: list[TraderRank], top_n: int) -> list[TraderRank]:
    """Trie par score de régularité décroissant et garde les top_n."""
    scored = [t.model_copy(update={"score": regularity_score(t)}) for t in traders]
    scored.sort(key=lambda t: (t.score, t.profitable_months_pct, t.gain), reverse=True)
    return scored[:top_n]


# --- Appels API --------------------------------------------------------------------------------
async def _fetch_rankings_page(settings: Settings, client: httpx.AsyncClient, page: int) -> dict[str, Any]:
    params = {"period": RANKINGS_PERIOD, "sort": RANKINGS_SORT, "page": page, "pageSize": RANKINGS_PAGE_SIZE}
    resp = await client.get(
        f"{settings.base_url}{ENDPOINTS['rankings']}",
        params=params,
        headers=_headers(settings),
        timeout=settings.etoro_timeout_s,
    )
    resp.raise_for_status()
    return resp.json()


async def _fetch_all_rankings(settings: Settings, client: httpx.AsyncClient) -> list[TraderRank]:
    rows: list[TraderRank] = []
    for page in range(1, RANKINGS_MAX_PAGES + 1):
        payload = await _fetch_rankings_page(settings, client, page)
        results = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(results, list):
            break
        for raw in results:
            if isinstance(raw, dict):
                rows.append(TraderRank.from_api(raw))
        pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
        if not pagination.get("hasNext") or len(results) < RANKINGS_PAGE_SIZE:
            break
    return rows


async def get_top_traders(settings: Settings, client: httpx.AsyncClient) -> list[TraderRank]:
    """Top N traders réguliers sur 12 mois (cache 1 h). Retourne [] en cas d'erreur."""
    key = (
        f"top:{settings.base_url}:{RANKINGS_PERIOD}:{settings.rankings_top_n}:"
        f"{settings.rankings_max_drawdown_pct}:{settings.rankings_min_profitable_months_pct}"
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        raw = await _fetch_all_rankings(settings, client)
    except Exception as exc:  # noqa: BLE001 - jamais d'exception vers l'appelant
        logger.warning("rankings: échec récupération du classement : %s", exc)
        return []
    top = rank_traders(filter_traders(raw, settings), settings.rankings_top_n)
    logger.info("rankings: %d candidats, %d retenus après filtres, top %d", len(raw), len(top), settings.rankings_top_n)
    _cache_set(key, top)
    return top


async def get_trader_positions(username: str, settings: Settings, client: httpx.AsyncClient) -> list[dict[str, Any]] | None:
    """Positions ouvertes publiques d'un trader (cache 1 h). None si indisponible (profil privé, erreur)."""
    key = f"portfolio:{settings.base_url}:{username.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        resp = await client.get(
            f"{settings.base_url}{ENDPOINTS['user_portfolio'].format(username=username)}",
            headers=_headers(settings),
            timeout=settings.etoro_timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rankings: portfolio de %s indisponible : %s", username, exc)
        return None
    positions = payload.get("positions") if isinstance(payload, dict) else None
    positions = [p for p in (positions or []) if isinstance(p, dict)]
    _cache_set(key, positions)
    return positions


def _is_exposed(positions: list[dict[str, Any]], instrument_id: int, side: Side | None) -> bool:
    for p in positions:
        if _as_int(_pick(p, "instrumentId", "InstrumentID", "instrumentID")) != instrument_id:
            continue
        if side is None:
            return True
        is_buy = _pick(p, "isBuy", "IsBuy")
        if is_buy is None or bool(is_buy) == (side == Side.BUY):
            return True
    return False


async def get_confirmation(
    instrument_id: int,
    settings: Settings,
    client: httpx.AsyncClient,
    side: Side | None = None,
) -> float | None:
    """Part des top traders exposés sur `instrument_id`, dans [0, 1]. None si indisponible.

    `side` (optionnel, hors contrat) restreint aux positions dans le même sens que le signal.
    Les traders dont le portfolio est inaccessible sont exclus du dénominateur.
    """
    try:
        top = await get_top_traders(settings, client)
        if not top:
            logger.info("rankings: aucun trader retenu, confirmation indisponible")
            return None

        sem = asyncio.Semaphore(PORTFOLIO_CONCURRENCY)

        async def _one(t: TraderRank) -> list[dict[str, Any]] | None:
            async with sem:
                return await get_trader_positions(t.username, settings, client)

        portfolios = await asyncio.gather(*(_one(t) for t in top))
        inspected = [p for p in portfolios if p is not None]
        if not inspected:
            logger.info("rankings: aucun portfolio lisible, confirmation indisponible")
            return None
        exposed = sum(1 for p in inspected if _is_exposed(p, instrument_id, side))
        ratio = exposed / len(inspected)
        logger.info("rankings: instrument %s -> %d/%d exposés (%.2f)", instrument_id, exposed, len(inspected), ratio)
        return max(0.0, min(1.0, ratio))
    except Exception as exc:  # noqa: BLE001 - jamais d'exception vers l'appelant
        logger.warning("rankings: erreur inattendue pour l'instrument %s : %s", instrument_id, exc)
        return None


__all__ = [
    "ENDPOINTS",
    "TraderRank",
    "clear_cache",
    "filter_traders",
    "get_confirmation",
    "get_top_traders",
    "get_trader_positions",
    "rank_traders",
    "regularity_score",
]
