"""Agent Portfolio eToro : boucle de décision (signaux -> garde-fous -> ordres -> état -> SMS).

Le module ne dépend des autres composants (`etoro_service`, `risk_guard`, `state_store`,
`notifier`, `rankings`, `news_feed`) que par leurs interfaces décrites dans CONTRACT.md :
tout est injecté dans le constructeur, ce qui permet de tester l'agent avec des fakes.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from etoro.config import Settings
from etoro.decision_log import DecisionLog, new_record
from etoro.models import (
    ClosedPosition,
    DailyStats,
    Instrument,
    OrderRequest,
    Position,
    Quote,
    RiskDecision,
    Side,
    Signal,
)

if TYPE_CHECKING:  # annotations seulement : ces modules peuvent ne pas exister encore
    from etoro.etoro_service import EtoroService
    from etoro.notifier import Notifier
    from etoro.risk_guard import RiskGuard
    from etoro.state_store import StateStore

logger = logging.getLogger(__name__)

SignalProvider = Callable[[], Awaitable[list[Signal]]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


class PortfolioAgent:
    """Orchestre un cycle de trading : sync des positions, filtrage des signaux, exécution."""

    def __init__(
        self,
        settings: Settings,
        service: EtoroService,
        guard: RiskGuard,
        store: StateStore,
        notifier: Notifier,
        instruments: dict[str, Instrument] | None = None,
        http_client: Any | None = None,
        decision_log: DecisionLog | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        self.guard = guard
        self.store = store
        self.notifier = notifier
        self.decision_log = decision_log  # journal des décisions (optionnel) : None = pas de journal
        self.instruments_by_symbol: dict[str, Instrument] = {}
        self.instruments_by_id: dict[int, Instrument] = {}
        if instruments:
            self._index_instruments(instruments.values())
        self._http_client = http_client  # httpx.AsyncClient partagé pour rankings/news (créé à la demande)
        self.trades_opened_today = 0
        self.trades_closed_today = 0
        self.last_summary_date: date | None = None
        self._running = False

    # ------------------------------------------------------------------ journal
    def _log(self, kind: str, reason: str, action: str, signal: Signal | None = None, **fields: Any) -> None:
        """Enregistre une décision dans le journal (no-op sans journal, ne lève jamais)."""
        if self.decision_log is None:
            return
        try:
            data: dict[str, Any] = {"mode": self.settings.etoro_mode}
            if signal is not None:
                data.update(
                    symbol=signal.symbol,
                    instrument_id=signal.instrument_id,
                    side=signal.side,
                    market_score=signal.market_score,
                    rankings_confirmation=signal.rankings_confirmation,
                    sentiment=signal.sentiment,
                )
            data.update(fields)
            self.decision_log.record(new_record(kind, reason, action, **data))
        except Exception:  # noqa: BLE001 - le journal ne doit jamais bloquer l'agent
            logger.exception("Journal des décisions : enregistrement impossible (%s/%s)", kind, reason)

    def _symbol_for(self, instrument_id: int) -> str:
        inst = self.instruments_by_id.get(instrument_id)
        return inst.symbol if inst is not None else str(instrument_id)

    # ------------------------------------------------------------------ univers
    def _index_instruments(self, instruments: Iterable[Instrument]) -> None:
        for inst in instruments:
            self.instruments_by_symbol[inst.symbol.upper()] = inst
            self.instruments_by_id[inst.instrument_id] = inst

    async def load_universe(self) -> dict[str, Instrument]:
        """Charge les instruments de `settings.universe` via l'API et construit les index."""
        instruments = await self.service.get_instruments(self.settings.universe)
        self.instruments_by_symbol.clear()
        self.instruments_by_id.clear()
        self._index_instruments(instruments)
        logger.info("Univers chargé : %s", sorted(self.instruments_by_symbol))
        return dict(self.instruments_by_symbol)

    # ------------------------------------------------------------------ cycle
    async def _maybe_reset_day(self, today: date | None = None) -> bool:
        """Réinitialise le PnL journalier si la date UTC a changé. Retourne True si reset."""
        today = today or _utcnow().date()
        if self.store.daily_pnl_date == today:
            return False
        equity = await self.service.get_account_balance()
        self.store.reset_day(equity, today)
        self.trades_opened_today = 0
        self.trades_closed_today = 0
        logger.info("Nouvelle journée %s : equity de départ %.2f", today, equity)
        return True

    @staticmethod
    def _refuse_all(signals: list[Signal], reason: str) -> list[RiskDecision]:
        return [RiskDecision(allowed=False, reason=reason) for _ in signals]

    async def run_once(self, signals: list[Signal]) -> list[RiskDecision]:
        """Traite les signaux (triés par score décroissant) et retourne une décision par signal.

        Les décisions sont renvoyées dans l'ordre de traitement (score décroissant).
        """
        if self.guard.kill_switch_active():
            logger.warning("Kill switch actif : %d signal(s) ignoré(s), aucun appel API", len(signals))
            if signals:  # un seul record par cycle, pas un par signal
                self._log("kill_switch", "kill_switch", f"Cycle ignoré : kill switch actif ({len(signals)} signal(s))")
            return self._refuse_all(signals, "kill_switch")
        if self.guard.is_breaker_active():
            logger.warning("Breaker actif : %d signal(s) ignoré(s), aucun appel API", len(signals))
            if signals:
                self._log("breaker", "breaker_active", f"Cycle ignoré : breaker actif ({len(signals)} signal(s))")
            return self._refuse_all(signals, "breaker_active")

        await self._maybe_reset_day()
        equity = await self.service.get_account_balance()
        open_positions: list[Position] = list(await self.service.get_open_positions())
        opened_this_cycle: set[int] = set()
        decisions: list[RiskDecision] = []

        for signal in sorted(signals, key=lambda s: s.market_score, reverse=True):
            self._log("signal", "received", "Analyse", signal=signal)
            if signal.instrument_id in opened_this_cycle:
                decision = RiskDecision(allowed=False, reason="already_opened_this_cycle")
            else:
                try:
                    decision = await self._process_signal(signal, equity, open_positions)
                except Exception:  # noqa: BLE001 - un signal en erreur ne doit pas bloquer les autres
                    logger.exception("Erreur d'exécution sur %s (id=%s)", signal.symbol, signal.instrument_id)
                    decision = RiskDecision(allowed=False, reason="execution_error")
                    self._log("error", "execution_error", "Erreur d'exécution, aucune action", signal=signal)
            if decision.allowed:
                opened_this_cycle.add(signal.instrument_id)
            elif decision.reason != "execution_error":
                self._log("skip", decision.reason, "Aucune action", signal=signal)
            decisions.append(decision)
        return decisions

    async def _process_signal(
        self, signal: Signal, equity: float, open_positions: list[Position]
    ) -> RiskDecision:
        await self._enrich_signal(signal)

        if (
            self.settings.use_rankings_confirmation
            and signal.rankings_confirmation is not None
            and signal.rankings_confirmation < self.settings.rankings_min_confirmation
        ):
            logger.info("%s : confirmation rankings %.2f insuffisante", signal.symbol, signal.rankings_confirmation)
            return RiskDecision(allowed=False, reason="rankings_not_confirmed")
        if (
            self.settings.use_news_sentiment
            and signal.sentiment is not None
            and signal.sentiment < self.settings.news_min_sentiment
        ):
            logger.info("%s : sentiment %.2f trop négatif", signal.symbol, signal.sentiment)
            return RiskDecision(allowed=False, reason="negative_sentiment")

        quote = await self.service.get_quote(signal.instrument_id)
        order = self.build_order(signal, quote, equity)
        decision = self.guard.check_open(signal, order, open_positions, equity, now=_utcnow())
        if not decision.allowed:
            logger.info("%s refusé par RiskGuard : %s", signal.symbol, decision.reason)
            return decision

        pos = await self.service.open_position(order)
        now = _utcnow()
        self.store.record_open(pos.instrument_id, pos.position_id, now)
        self.store.save()
        open_positions.append(pos)
        self.trades_opened_today += 1
        logger.info(
            "Position ouverte %s %s amount=%.2f open_rate=%.4f", pos.side.value, signal.symbol, pos.amount, pos.open_rate
        )
        await self.notifier.send_position_opened(pos, self.instruments_by_id.get(pos.instrument_id))
        sl = pos.stop_loss_rate if pos.stop_loss_rate is not None else order.stop_loss_rate
        tp = pos.take_profit_rate if pos.take_profit_rate is not None else order.take_profit_rate
        self._log(
            "open",
            "opened",
            f"Ouverture {pos.side.value} {signal.symbol} {pos.amount:.2f} USD @ {pos.open_rate:.4f}"
            f" SL {_fmt_rate(sl)} TP {_fmt_rate(tp)}",
            signal=signal,
            position_id=pos.position_id,
            amount=pos.amount,
            entry_rate=pos.open_rate,
            stop_loss_rate=sl,
            take_profit_rate=tp,
        )
        return decision

    def build_order(self, signal: Signal, quote: Quote, equity: float) -> OrderRequest:
        """Construit l'OrderRequest : entry = ask (BUY) / bid (SELL), SL/TP en % du prix d'entrée."""
        sl = self.settings.etoro_sl_pct / 100.0
        tp = self.settings.etoro_tp_pct / 100.0
        if signal.side == Side.BUY:
            entry = quote.ask
            stop_loss = entry * (1 - sl)
            take_profit = entry * (1 + tp)
        else:
            entry = quote.bid
            stop_loss = entry * (1 + sl)
            take_profit = entry * (1 - tp)
        amount = round(equity * self.settings.etoro_position_size_pct / 100.0, 2)
        return OrderRequest(
            instrument_id=signal.instrument_id,
            side=signal.side,
            amount=amount,
            leverage=1,
            entry_rate=entry,
            stop_loss_rate=round(stop_loss, 4),
            take_profit_rate=round(take_profit, 4),
            score=signal.market_score,
        )

    # ------------------------------------------------------------------ enrichissement
    def _get_http_client(self) -> Any:
        if self._http_client is None:
            import httpx  # dépendance déclarée ; import local pour rester léger dans les tests

            self._http_client = httpx.AsyncClient(timeout=self.settings.etoro_timeout_s)
        return self._http_client

    async def _fetch_rankings_confirmation(self, signal: Signal) -> float | None:
        """Appelle rankings.get_confirmation si le module est disponible ; None sinon."""
        try:
            from etoro import rankings
        except ImportError:
            logger.debug("Module rankings indisponible : pas de confirmation")
            return None
        try:
            return await rankings.get_confirmation(signal.instrument_id, self.settings, self._get_http_client())
        except Exception:  # noqa: BLE001
            logger.warning("rankings.get_confirmation a échoué pour %s", signal.symbol, exc_info=True)
            return None

    async def _fetch_sentiment(self, signal: Signal) -> float | None:
        """Appelle news_feed.get_sentiment si le module est disponible ; None sinon."""
        try:
            from etoro import news_feed
        except ImportError:
            logger.debug("Module news_feed indisponible : pas de sentiment")
            return None
        try:
            return await news_feed.get_sentiment(signal.symbol, self.settings, self._get_http_client())
        except Exception:  # noqa: BLE001
            logger.warning("news_feed.get_sentiment a échoué pour %s", signal.symbol, exc_info=True)
            return None

    async def _enrich_signal(self, signal: Signal) -> None:
        if self.settings.use_rankings_confirmation and signal.rankings_confirmation is None:
            signal.rankings_confirmation = await self._fetch_rankings_confirmation(signal)
        if self.settings.use_news_sentiment and signal.sentiment is None:
            signal.sentiment = await self._fetch_sentiment(signal)

    # ------------------------------------------------------------------ positions
    async def sync_positions(self) -> list[ClosedPosition]:
        """Détecte les positions du store qui ont disparu côté eToro (SL/TP touchés, fermeture manuelle)."""
        current = await self.service.get_open_positions()
        live_ids = {p.position_id for p in current}
        closed: list[ClosedPosition] = []
        get_closed = getattr(self.service, "get_closed_position", None)

        for instrument_id, position_id in list(self.store.open_positions_by_instrument.items()):
            if position_id in live_ids:
                continue
            now = _utcnow()
            realized_pnl = 0.0
            if callable(get_closed):
                try:
                    detail = await get_closed(position_id)
                    realized_pnl = float(getattr(detail, "realized_pnl", detail))
                except Exception:  # noqa: BLE001
                    logger.warning("PnL réalisé indisponible pour %s, 0.0 utilisé", position_id, exc_info=True)
            else:
                logger.warning("service.get_closed_position absent : PnL réalisé de %s fixé à 0.0", position_id)

            closed_pos = ClosedPosition(
                position_id=position_id, instrument_id=instrument_id, realized_pnl=realized_pnl, closed_at=now
            )
            decision = self.guard.on_position_closed(closed_pos, now=now)
            self.store.record_close(instrument_id, now)
            self.store.save()
            self.trades_closed_today += 1
            closed.append(closed_pos)
            logger.info("Position fermée %s (instrument %s) pnl=%.2f", position_id, instrument_id, realized_pnl)
            symbol = self._symbol_for(instrument_id)
            self._log(
                "close",
                "closed",
                f"Fermeture {symbol} PnL {realized_pnl:+.2f} USD",
                symbol=symbol,
                instrument_id=instrument_id,
                position_id=position_id,
                realized_pnl=realized_pnl,
            )
            await self.notifier.send_position_closed(closed_pos)
            if decision.reason == "breaker_tripped":
                pct = self._daily_pnl_pct()
                logger.error("Breaker déclenché : PnL jour %.2f%%", pct)
                self._log(
                    "breaker",
                    "breaker_tripped",
                    f"Pause {self.settings.etoro_breaker_pause_hours:g}h (PnL jour {pct:+.2f}%)",
                    symbol=symbol,
                    instrument_id=instrument_id,
                    position_id=position_id,
                    realized_pnl=realized_pnl,
                )
                await self.notifier.send_breaker_tripped(pct)
        return closed

    def _daily_pnl_pct(self) -> float:
        start = self.store.equity_start_of_day or 0.0
        if start <= 0:
            return 0.0
        return round(self.store.daily_pnl / start * 100.0, 2)

    # ------------------------------------------------------------------ résumé & boucle
    def build_daily_stats(self) -> DailyStats:
        today = self.store.daily_pnl_date or _utcnow().date()
        return DailyStats(
            date=today.isoformat(),
            trades_opened=self.trades_opened_today,
            trades_closed=self.trades_closed_today,
            realized_pnl=round(self.store.daily_pnl, 2),
            realized_pnl_pct=self._daily_pnl_pct(),
            open_positions=len(self.store.open_positions_by_instrument),
            breaker_active=self.guard.is_breaker_active(),
            kill_switch=self.guard.kill_switch_active(),
            mode=self.settings.etoro_mode,
        )

    async def _maybe_send_daily_summary(self) -> None:
        """Envoie un résumé une fois par jour UTC (au premier cycle du jour suivant)."""
        today = _utcnow().date()
        if self.last_summary_date == today:
            return
        if self.last_summary_date is None:
            # premier cycle après démarrage : pas de résumé pour une journée partielle
            self.last_summary_date = today
            return
        await self.notifier.send_daily_summary(self.build_daily_stats())
        self.last_summary_date = today

    async def run_forever(self, signal_provider: SignalProvider, interval_s: int = 60) -> None:
        """Boucle principale : sync -> signaux -> run_once -> résumé quotidien -> sleep."""
        self._running = True
        logger.info("Agent Portfolio démarré (mode=%s, interval=%ss)", self.settings.etoro_mode, interval_s)
        try:
            while self._running:
                try:
                    await self.sync_positions()
                    signals = await signal_provider()
                    decisions = await self.run_once(signals)
                    opened = sum(1 for d in decisions if d.allowed)
                    logger.debug("Cycle terminé : %d signal(s), %d ouverture(s)", len(signals), opened)
                    await self._maybe_send_daily_summary()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("Erreur dans le cycle de l'agent, reprise au prochain intervalle")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("Agent Portfolio arrêté proprement")
        finally:
            self._running = False

    def stop(self) -> None:
        """Demande l'arrêt de la boucle après le cycle courant."""
        self._running = False

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None


# ---------------------------------------------------------------------- helpers
async def default_signal_provider_from_scores(scores: dict[str, float], agent: PortfolioAgent) -> list[Signal]:
    """Transforme `{symbol: market_score}` (sortie d'indicator_engine) en signaux BUY.

    Choix v1 : uniquement des signaux BUY. Le score de marché de CryptoMind mesure la
    force haussière ; le short (SELL/CFD) ajoute un risque de perte illimitée et des frais
    overnight, il est volontairement exclu de la première version. Les symboles absents
    de l'univers chargé sont ignorés avec un warning. Le filtrage par `etoro_min_score`
    est laissé à RiskGuard (`score_below_min`).
    """
    now = _utcnow()
    signals: list[Signal] = []
    for symbol, score in scores.items():
        inst = agent.instruments_by_symbol.get(symbol.upper())
        if inst is None:
            logger.warning("Symbole %s hors univers eToro, ignoré", symbol)
            continue
        signals.append(
            Signal(
                instrument_id=inst.instrument_id,
                symbol=inst.symbol,
                side=Side.BUY,
                market_score=max(0.0, min(100.0, float(score))),
                generated_at=now,
            )
        )
    return signals


async def _noop_signal_provider() -> list[Signal]:
    """Provider factice pour `python -m etoro.agent` : ne produit aucun signal (aucun trade)."""
    return []


async def main() -> None:
    """Worker autonome (Railway) : sync des positions + résumé quotidien, sans signaux réels."""
    from etoro.config import get_settings
    from etoro.etoro_service import EtoroService
    from etoro.notifier import Notifier
    from etoro.risk_guard import RiskGuard
    from etoro.state_store import StateStore

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    service = EtoroService(settings)
    store = StateStore(settings.etoro_state_path)
    store.load()
    guard = RiskGuard(settings, store)
    notifier = Notifier(settings)
    decision_log = DecisionLog(settings.etoro_decisions_path)
    decision_log.load_tail()
    agent = PortfolioAgent(settings, service, guard, store, notifier, decision_log=decision_log)
    try:
        await agent.load_universe()
        await agent.run_forever(_noop_signal_provider, interval_s=60)
    finally:
        await agent.aclose()
        await service.aclose()


if __name__ == "__main__":
    asyncio.run(main())
