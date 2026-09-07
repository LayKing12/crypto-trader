"""Veille des prix Kraken + eToro : observation passive des mouvements significatifs.

À chaque tick, `MarketWatch` lit les prix de chaque actif suivi, les compare à la dernière
référence mémorisée et, si |variation| >= `WATCH_MOVE_THRESHOLD_PCT`, enregistre une
Observation `price_move` puis remplace la référence. Aucune action n'est déclenchée : ce
module ne connaît ni les signaux, ni le risk guard, ni les ordres.

Injection :
    kraken_price_fn : callable async `symbol -> float | None`
                      (en production `app.services.market_data_service.get_price`)
    etoro_service   : objet exposant `get_instruments(symbols)` et `get_quotes(ids)`
                      (en production `etoro.etoro_service.EtoroService`, optionnel)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .config import WatchSettings
from .observation_log import Observation, ObservationLog, new_observation

log = logging.getLogger(__name__)

KrakenPriceFn = Callable[[str], Awaitable[float | None]]


def _mid(quote: Any) -> float | None:
    """Prix milieu d'un Quote eToro (bid/ask) ; None si inexploitable."""
    try:
        bid = float(getattr(quote, "bid"))
        ask = float(getattr(quote, "ask"))
    except (TypeError, ValueError, AttributeError):
        return None
    if bid <= 0 and ask <= 0:
        return None
    if bid <= 0:
        return ask
    if ask <= 0:
        return bid
    return (bid + ask) / 2.0


class MarketWatch:
    """Compare les prix courants aux références mémorisées, par (source, symbole)."""

    def __init__(
        self,
        settings: WatchSettings,
        obs_log: ObservationLog,
        kraken_price_fn: KrakenPriceFn | None = None,
        etoro_service: Any = None,
    ) -> None:
        self._settings = settings
        self._log = obs_log
        self._kraken_price_fn = kraken_price_fn
        self._etoro = etoro_service
        self._refs: dict[tuple[str, str], float] = {}          # (source, symbol) -> prix de référence
        self._smallcaps: dict[str, dict[str, Any]] = {}          # paire Kraken -> {symbol, rank, name} (observation seule)
        self._etoro_ids: dict[int, str] | None = None           # instrument_id -> symbole (résolu 1 fois)
        self._source_failing: dict[str, bool] = {}              # source -> erreur déjà journalisée
        self.last_tick_at: datetime | None = None
        self.tick_count = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ état
    @property
    def threshold_pct(self) -> float:
        return self._settings.watch_move_threshold_pct

    def reference(self, source: str, symbol: str) -> float | None:
        return self._refs.get((source, symbol.upper()))

    def references(self) -> dict[str, float]:
        return {f"{src}:{sym}": price for (src, sym), price in sorted(self._refs.items())}

    def status(self) -> dict[str, Any]:
        return {
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "tick_count": self.tick_count,
            "last_error": self.last_error,
            "threshold_pct": self.threshold_pct,
            "kraken_pairs": self._settings.kraken_pairs if self._kraken_price_fn else [],
            "smallcaps": len(self._smallcaps),
            "smallcap_pairs": sorted(self._smallcaps),
            "etoro_symbols": self._settings.etoro_symbols if self._etoro is not None else [],
            "tracked": len(self._refs),
        }

    # ------------------------------------------------------------------ cœur
    def _compare(self, source: str, symbol: str, price: float) -> Observation | None:
        """Met à jour la référence et retourne une observation si le seuil est franchi."""
        key = (source, symbol.upper())
        prev = self._refs.get(key)
        if prev is None or prev <= 0:
            self._refs[key] = price
            return None
        change_pct = (price - prev) / prev * 100.0
        if abs(change_pct) < self.threshold_pct:
            return None
        self._refs[key] = price
        direction = "hausse" if change_pct > 0 else "baisse"
        obs = new_observation(
            "price_move", source,  # type: ignore[arg-type]
            title=f"{symbol.upper()} {direction} de {change_pct:+.2f} % ({source})",
            symbol=symbol,
            detail={
                "symbol": symbol.upper(),
                "source": source,
                "prev": prev,
                "last": price,
                "change_pct": round(change_pct, 4),
                "threshold_pct": self.threshold_pct,
            },
        )
        return self._log.record(obs)

    def _note_source_error(self, source: str, exc: BaseException) -> None:
        self.last_error = f"{source}: {type(exc).__name__}: {exc}"
        log.warning("watch market : source %s en erreur (%s)", source, exc)
        if not self._source_failing.get(source):
            self._source_failing[source] = True
            self._log.record(new_observation(
                "error", source,  # type: ignore[arg-type]
                title=f"Veille {source} : {type(exc).__name__}",
                detail={"error": str(exc)[:500], "type": type(exc).__name__},
            ))

    def _note_source_ok(self, source: str) -> None:
        self._source_failing[source] = False

    def set_smallcaps(self, items: list[dict[str, Any]]) -> None:
        """Paires small caps à observer (jamais transmises au moteur de décision)."""
        self._smallcaps = {str(i["pair"]): dict(i) for i in items if i.get("pair")}

    @property
    def kraken_watch_pairs(self) -> list[str]:
        base = list(self._settings.kraken_pairs)
        return base + [p for p in self._smallcaps if p not in base]

    async def _tick_kraken(self) -> list[Observation]:
        if self._kraken_price_fn is None:
            return []
        moves: list[Observation] = []
        failures = 0
        last_exc: BaseException | None = None
        pairs = self.kraken_watch_pairs
        for pair in pairs:
            try:
                price = await self._kraken_price_fn(pair)
            except Exception as exc:  # noqa: BLE001 - une paire en erreur n'arrête pas les autres
                failures += 1
                last_exc = exc
                log.debug("watch market kraken %s : %s", pair, exc)
                continue
            if price is None or price <= 0:
                continue
            obs = self._compare("kraken", pair, float(price))
            if obs is not None:
                sc = self._smallcaps.get(pair)
                if sc:
                    obs.detail.update({"smallcap": True, "rank": sc.get("rank"), "name": sc.get("name"), "coin": sc.get("symbol")})
                moves.append(obs)
        if failures and failures == len(pairs) and last_exc is not None:
            self._note_source_error("kraken", last_exc)
        else:
            self._note_source_ok("kraken")
        return moves

    async def _resolve_etoro_ids(self) -> dict[int, str]:
        if self._etoro_ids is None:
            instruments = await self._etoro.get_instruments(self._settings.etoro_symbols)
            ids: dict[int, str] = {}
            for inst in instruments or []:
                try:
                    ids[int(getattr(inst, "instrument_id"))] = str(getattr(inst, "symbol")).upper()
                except (TypeError, ValueError, AttributeError):
                    continue
            if ids:
                self._etoro_ids = ids
            return ids
        return self._etoro_ids

    async def _tick_etoro(self) -> list[Observation]:
        if self._etoro is None or not self._settings.etoro_symbols:
            return []
        moves: list[Observation] = []
        ids = await self._resolve_etoro_ids()
        if not ids:
            return moves
        quotes = await self._etoro.get_quotes(list(ids))
        for q in quotes or []:
            symbol = ids.get(int(getattr(q, "instrument_id", -1)))
            if symbol is None:
                continue
            price = _mid(q)
            if price is None:
                continue
            obs = self._compare("etoro", symbol, price)
            if obs is not None:
                moves.append(obs)
        return moves

    async def tick(self) -> list[Observation]:
        """Un cycle complet. Une source en erreur n'empêche jamais l'autre. Ne lève jamais."""
        moves: list[Observation] = []
        try:
            moves.extend(await self._tick_kraken())
        except Exception as exc:  # noqa: BLE001
            self._note_source_error("kraken", exc)
        try:
            moves.extend(await self._tick_etoro())
            if self._etoro is not None:
                self._note_source_ok("etoro")
        except Exception as exc:  # noqa: BLE001
            self._note_source_error("etoro", exc)
        self.tick_count += 1
        self.last_tick_at = datetime.now(UTC)
        if moves:
            log.info("watch market : %d mouvement(s) >= %.2f %%", len(moves), self.threshold_pct)
        return moves

    async def run_forever(self, interval_s: float | None = None) -> None:
        """Boucle infinie ; chaque tick est protégé, seule l'annulation asyncio l'arrête."""
        interval = float(interval_s if interval_s is not None else self._settings.watch_interval_s)
        log.info("watch market : démarrage (intervalle %.0fs, seuil %.2f %%)", interval, self.threshold_pct)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - ceinture et bretelles, tick() ne lève pas
                self.last_error = f"tick: {exc}"
                log.exception("watch market : tick en erreur")
            await asyncio.sleep(interval)
