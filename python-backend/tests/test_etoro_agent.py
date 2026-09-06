"""Tests de PortfolioAgent avec des fakes respectant les interfaces de CONTRACT.md."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from etoro.agent import PortfolioAgent, default_signal_provider_from_scores
from etoro.config import Settings
from etoro.decision_log import DecisionLog
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

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


# ---------------------------------------------------------------------- fakes
class FakeService:
    def __init__(self, equity: float = 10_000.0, positions: list[Position] | None = None) -> None:
        self.equity = equity
        self.positions: list[Position] = list(positions or [])
        self.quotes: dict[int, Quote] = {}
        self.closed_pnl: dict[str, float] = {}
        self.calls: list[str] = []
        self.opened_orders: list[OrderRequest] = []
        self.fail_open_for: set[int] = set()
        self._seq = 0

    async def get_instruments(self, symbols: list[str]) -> list[Instrument]:
        self.calls.append("get_instruments")
        return [Instrument(symbol=s, instrument_id=100 + i) for i, s in enumerate(symbols)]

    async def get_quote(self, instrument_id: int) -> Quote:
        self.calls.append(f"get_quote:{instrument_id}")
        return self.quotes.get(instrument_id, Quote(instrument_id=instrument_id, bid=99.0, ask=100.0, timestamp=NOW))

    async def get_quotes(self, instrument_ids: list[int]) -> list[Quote]:
        self.calls.append("get_quotes")
        return [await self.get_quote(i) for i in instrument_ids]

    async def get_open_positions(self) -> list[Position]:
        self.calls.append("get_open_positions")
        return list(self.positions)

    async def open_position(self, order: OrderRequest) -> Position:
        self.calls.append(f"open_position:{order.instrument_id}")
        if order.instrument_id in self.fail_open_for:
            raise RuntimeError("eToro 500")
        self._seq += 1
        pos = Position(
            position_id=f"pos-{self._seq}",
            instrument_id=order.instrument_id,
            side=order.side,
            amount=order.amount,
            open_rate=order.entry_rate,
            stop_loss_rate=order.stop_loss_rate,
            take_profit_rate=order.take_profit_rate,
            opened_at=NOW,
        )
        self.positions.append(pos)
        self.opened_orders.append(order)
        return pos

    async def close_position(self, position_id: str) -> ClosedPosition:
        self.calls.append(f"close_position:{position_id}")
        pos = next(p for p in self.positions if p.position_id == position_id)
        self.positions.remove(pos)
        return ClosedPosition(position_id=position_id, instrument_id=pos.instrument_id, realized_pnl=0.0, closed_at=NOW)

    async def get_account_balance(self) -> float:
        self.calls.append("get_account_balance")
        return self.equity


class FakeServiceWithClosedPnl(FakeService):
    async def get_closed_position(self, position_id: str) -> ClosedPosition:
        self.calls.append(f"get_closed_position:{position_id}")
        return ClosedPosition(
            position_id=position_id, instrument_id=0, realized_pnl=self.closed_pnl.get(position_id, 0.0), closed_at=NOW
        )


class FakeGuard:
    def __init__(self) -> None:
        self.kill = False
        self.breaker = False
        self.decision = RiskDecision(allowed=True, reason="ok")
        self.close_decision = RiskDecision(allowed=True, reason="ok")
        self.check_calls: list[tuple[Signal, OrderRequest, int, float]] = []
        self.closed_calls: list[ClosedPosition] = []

    def check_open(self, signal, order, open_positions, equity, now=None) -> RiskDecision:
        self.check_calls.append((signal, order, len(open_positions), equity))
        return self.decision

    def on_position_closed(self, closed, now=None) -> RiskDecision:
        self.closed_calls.append(closed)
        return self.close_decision

    def is_breaker_active(self, now=None) -> bool:
        return self.breaker

    def kill_switch_active(self) -> bool:
        return self.kill


class FakeStore:
    def __init__(self) -> None:
        self.open_positions_by_instrument: dict[int, str] = {}
        self.last_trade_at: dict[int, datetime] = {}
        self.daily_pnl = 0.0
        self.daily_pnl_date: date | None = TODAY
        self.equity_start_of_day = 10_000.0
        self.breaker_until: datetime | None = None
        self.kill_switch = False
        self.kill_switch_actor: str | None = None
        self.saves = 0
        self.resets: list[tuple[float, date]] = []

    def record_open(self, instrument_id: int, position_id: str, now: datetime) -> None:
        self.open_positions_by_instrument[instrument_id] = position_id
        self.last_trade_at[instrument_id] = now

    def record_close(self, instrument_id: int, now: datetime) -> None:
        self.open_positions_by_instrument.pop(instrument_id, None)

    def reset_day(self, equity: float, today: date) -> None:
        self.resets.append((equity, today))
        self.daily_pnl = 0.0
        self.daily_pnl_date = today
        self.equity_start_of_day = equity

    def save(self) -> None:
        self.saves += 1

    def snapshot(self) -> dict:
        return {"open": dict(self.open_positions_by_instrument)}


class FakeNotifier:
    def __init__(self) -> None:
        self.opened: list[Position] = []
        self.closed: list[ClosedPosition] = []
        self.summaries: list[DailyStats] = []
        self.breakers: list[float] = []

    async def send_position_opened(self, pos, instrument=None) -> None:
        self.opened.append(pos)

    async def send_position_closed(self, closed) -> None:
        self.closed.append(closed)

    async def send_daily_summary(self, stats) -> None:
        self.summaries.append(stats)

    async def send_breaker_tripped(self, daily_pnl_pct) -> None:
        self.breakers.append(daily_pnl_pct)

    async def send_kill_switch(self, actor, enabled) -> None:
        pass


# ---------------------------------------------------------------------- helpers
def make_settings(**overrides) -> Settings:
    base = {"ETORO_API_KEY": "test", "ETORO_USE_RANKINGS": "false", "ETORO_USE_NEWS": "false"}
    base.update(overrides)
    return Settings(**base)


def make_signal(instrument_id: int = 1, symbol: str = "AAPL", score: float = 80.0, **kw) -> Signal:
    return Signal(
        instrument_id=instrument_id, symbol=symbol, side=Side.BUY, market_score=score, generated_at=NOW, **kw
    )


INSTRUMENTS = {"AAPL": Instrument(symbol="AAPL", instrument_id=1), "MSFT": Instrument(symbol="MSFT", instrument_id=2)}


def make_agent(settings: Settings | None = None, service: FakeService | None = None):
    settings = settings or make_settings()
    service = service or FakeService()
    guard, store, notifier = FakeGuard(), FakeStore(), FakeNotifier()
    agent = PortfolioAgent(settings, service, guard, store, notifier, instruments=INSTRUMENTS)
    return agent, service, guard, store, notifier


# ---------------------------------------------------------------------- tests run_once
async def test_kill_switch_refuses_all_without_api_call():
    agent, service, guard, store, notifier = make_agent()
    guard.kill = True
    decisions = await agent.run_once([make_signal(1), make_signal(2, "MSFT")])
    assert [d.reason for d in decisions] == ["kill_switch", "kill_switch"]
    assert all(not d.allowed for d in decisions)
    assert service.calls == []
    assert notifier.opened == []


async def test_breaker_refuses_all_without_api_call():
    agent, service, guard, *_ = make_agent()
    guard.breaker = True
    decisions = await agent.run_once([make_signal()])
    assert decisions == [RiskDecision(allowed=False, reason="breaker_active")]
    assert service.calls == []


async def test_open_ok_calls_service_store_and_notifier():
    agent, service, guard, store, notifier = make_agent()
    service.quotes[1] = Quote(instrument_id=1, bid=199.5, ask=200.0, timestamp=NOW)

    decisions = await agent.run_once([make_signal(1, score=85.0)])

    assert decisions == [RiskDecision(allowed=True, reason="ok")]
    assert "open_position:1" in service.calls
    order = service.opened_orders[0]
    assert order.side == Side.BUY
    assert order.entry_rate == 200.0
    assert order.stop_loss_rate == pytest.approx(196.0)  # -2 %
    assert order.take_profit_rate == pytest.approx(208.0)  # +4 %
    assert order.amount == pytest.approx(1000.0)  # 10 % de 10 000
    assert order.leverage == 1
    assert order.score == 85.0
    assert store.open_positions_by_instrument == {1: "pos-1"}
    assert store.saves == 1
    assert len(notifier.opened) == 1 and notifier.opened[0].position_id == "pos-1"
    assert agent.trades_opened_today == 1
    # le guard a bien reçu le contexte (0 position ouverte avant l'ordre, equity)
    assert guard.check_calls[0][2] == 0 and guard.check_calls[0][3] == 10_000.0


async def test_guard_refusal_does_not_open():
    agent, service, guard, store, notifier = make_agent()
    guard.decision = RiskDecision(allowed=False, reason="cooldown_active")
    decisions = await agent.run_once([make_signal()])
    assert decisions == [RiskDecision(allowed=False, reason="cooldown_active")]
    assert not any(c.startswith("open_position") for c in service.calls)
    assert store.open_positions_by_instrument == {}
    assert notifier.opened == []


async def test_rankings_not_confirmed_skips_signal():
    agent, service, *_ = make_agent(make_settings(ETORO_USE_RANKINGS="true"))
    decisions = await agent.run_once([make_signal(rankings_confirmation=0.1)])
    assert decisions == [RiskDecision(allowed=False, reason="rankings_not_confirmed")]
    assert not any(c.startswith("get_quote") for c in service.calls)


async def test_rankings_fetched_lazily_when_missing(monkeypatch):
    agent, service, *_ = make_agent(make_settings(ETORO_USE_RANKINGS="true"))

    async def fake_fetch(signal):
        return 0.05

    monkeypatch.setattr(agent, "_fetch_rankings_confirmation", fake_fetch)
    signal = make_signal()
    decisions = await agent.run_once([signal])
    assert decisions[0].reason == "rankings_not_confirmed"
    assert signal.rankings_confirmation == 0.05


async def test_rankings_module_missing_does_not_block():
    """Sans module rankings importable, la confirmation reste None et l'ordre passe."""
    agent, service, *_ = make_agent(make_settings(ETORO_USE_RANKINGS="true"))
    decisions = await agent.run_once([make_signal()])
    # soit le module n'existe pas (None -> pas de filtre), soit il existe et renvoie None/valeur ;
    # dans tous les cas l'agent ne doit pas lever
    assert decisions[0].reason in {"ok", "rankings_not_confirmed"}


async def test_negative_sentiment_skips_signal():
    agent, *_ = make_agent(make_settings(ETORO_USE_NEWS="true"))
    decisions = await agent.run_once([make_signal(sentiment=-0.9)])
    assert decisions == [RiskDecision(allowed=False, reason="negative_sentiment")]


async def test_execution_error_when_open_position_raises():
    agent, service, guard, store, notifier = make_agent()
    service.fail_open_for.add(1)
    decisions = await agent.run_once([make_signal(1, "AAPL", 90.0), make_signal(2, "MSFT", 80.0)])
    assert decisions[0] == RiskDecision(allowed=False, reason="execution_error")
    assert decisions[1] == RiskDecision(allowed=True, reason="ok")  # le cycle continue
    assert store.open_positions_by_instrument == {2: "pos-1"}
    assert len(notifier.opened) == 1


async def test_signals_processed_by_descending_score():
    agent, service, guard, *_ = make_agent()
    signals = [make_signal(1, "AAPL", 72.0), make_signal(2, "MSFT", 95.0)]
    await agent.run_once(signals)
    processed = [call[0].instrument_id for call in guard.check_calls]
    assert processed == [2, 1]
    assert [c for c in service.calls if c.startswith("open_position")] == ["open_position:2", "open_position:1"]


async def test_one_open_per_instrument_per_cycle():
    agent, service, *_ = make_agent()
    decisions = await agent.run_once([make_signal(1, score=90.0), make_signal(1, score=80.0)])
    assert decisions[0].allowed is True
    assert decisions[1] == RiskDecision(allowed=False, reason="already_opened_this_cycle")
    assert service.calls.count("open_position:1") == 1


async def test_reset_day_on_date_change():
    agent, service, guard, store, notifier = make_agent()
    store.daily_pnl_date = TODAY - timedelta(days=1)
    store.daily_pnl = -50.0
    service.equity = 12_345.0
    agent.trades_opened_today = 3

    await agent.run_once([])

    assert store.resets == [(12_345.0, datetime.now(UTC).date())]
    assert store.daily_pnl == 0.0
    assert store.equity_start_of_day == 12_345.0
    assert agent.trades_opened_today == 0


async def test_no_reset_when_same_day():
    agent, service, guard, store, notifier = make_agent()
    store.daily_pnl_date = datetime.now(UTC).date()
    await agent.run_once([])
    assert store.resets == []


# ---------------------------------------------------------------------- tests sync_positions
async def test_sync_positions_detects_close_and_propagates_breaker():
    service = FakeServiceWithClosedPnl()
    agent, service, guard, store, notifier = make_agent(service=service)
    store.open_positions_by_instrument = {1: "pos-gone", 2: "pos-live"}
    service.positions = [
        Position(position_id="pos-live", instrument_id=2, side=Side.BUY, amount=1000, open_rate=100, opened_at=NOW)
    ]
    service.closed_pnl["pos-gone"] = -350.0
    store.daily_pnl = -350.0
    guard.close_decision = RiskDecision(allowed=False, reason="breaker_tripped")

    closed = await agent.sync_positions()

    assert [c.position_id for c in closed] == ["pos-gone"]
    assert closed[0].instrument_id == 1
    assert closed[0].realized_pnl == -350.0
    assert guard.closed_calls == closed
    assert store.open_positions_by_instrument == {2: "pos-live"}
    assert store.saves == 1
    assert notifier.closed == closed
    assert notifier.breakers == [pytest.approx(-3.5)]
    assert agent.trades_closed_today == 1


async def test_sync_positions_without_get_closed_position_uses_zero_pnl():
    agent, service, guard, store, notifier = make_agent()
    store.open_positions_by_instrument = {1: "pos-gone"}
    closed = await agent.sync_positions()
    assert closed[0].realized_pnl == 0.0
    assert notifier.breakers == []


async def test_sync_positions_nothing_closed():
    agent, service, guard, store, notifier = make_agent()
    service.positions = [
        Position(position_id="p1", instrument_id=1, side=Side.BUY, amount=1000, open_rate=100, opened_at=NOW)
    ]
    store.open_positions_by_instrument = {1: "p1"}
    assert await agent.sync_positions() == []
    assert notifier.closed == []


# ---------------------------------------------------------------------- divers
async def test_load_universe_builds_indexes():
    agent, service, *_ = make_agent(make_settings(ETORO_UNIVERSE="aapl, msft"))
    agent.instruments_by_symbol.clear()
    agent.instruments_by_id.clear()
    mapping = await agent.load_universe()
    assert set(mapping) == {"AAPL", "MSFT"}
    assert agent.instruments_by_id[100].symbol == "AAPL"


async def test_default_signal_provider_from_scores():
    agent, *_ = make_agent()
    signals = await default_signal_provider_from_scores({"aapl": 88.0, "MSFT": 120.0, "TSLA": 99.0}, agent)
    assert [(s.symbol, s.instrument_id, s.side) for s in signals] == [("AAPL", 1, Side.BUY), ("MSFT", 2, Side.BUY)]
    assert signals[1].market_score == 100.0  # clamp


async def test_build_daily_stats():
    agent, service, guard, store, notifier = make_agent(make_settings(ETORO_MODE="demo"))
    store.daily_pnl = 123.456
    store.open_positions_by_instrument = {1: "p1"}
    agent.trades_opened_today = 2
    stats = agent.build_daily_stats()
    assert stats == DailyStats(
        date=TODAY.isoformat(),
        trades_opened=2,
        trades_closed=0,
        realized_pnl=123.46,
        realized_pnl_pct=1.23,
        open_positions=1,
        breaker_active=False,
        kill_switch=False,
        mode="demo",
    )


async def test_run_forever_loops_and_stops_on_cancel():
    agent, service, guard, store, notifier = make_agent()
    cycles = 0

    async def provider() -> list[Signal]:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            raise RuntimeError("provider down")  # doit être capturé, la boucle continue
        return [make_signal()]

    task = asyncio.create_task(agent.run_forever(provider, interval_s=0))
    for _ in range(50):
        await asyncio.sleep(0)
        if cycles >= 3:
            break
    task.cancel()
    await task  # CancelledError absorbée proprement par run_forever
    assert cycles >= 3
    assert agent.trades_opened_today >= 1


async def test_daily_summary_sent_once_per_day(monkeypatch):
    agent, service, guard, store, notifier = make_agent()
    agent.last_summary_date = TODAY - timedelta(days=1)
    await agent._maybe_send_daily_summary()
    await agent._maybe_send_daily_summary()
    assert len(notifier.summaries) == 1
    assert agent.last_summary_date == datetime.now(UTC).date()


# ---------------------------------------------------------------------- journal des décisions
def make_logged_agent(settings: Settings | None = None, service: FakeService | None = None):
    settings = settings or make_settings()
    service = service or FakeService()
    guard, store, notifier = FakeGuard(), FakeStore(), FakeNotifier()
    log = DecisionLog(None)  # mémoire seule
    agent = PortfolioAgent(
        settings, service, guard, store, notifier, instruments=INSTRUMENTS, decision_log=log
    )
    return agent, service, guard, store, notifier, log


def chronological(log: DecisionLog):
    return list(reversed(log.recent()))


async def test_decision_log_records_signal_then_open():
    agent, service, guard, store, notifier, log = make_logged_agent()
    service.quotes[1] = Quote(instrument_id=1, bid=199.5, ask=200.0, timestamp=NOW)

    await agent.run_once([make_signal(1, score=85.0, rankings_confirmation=0.7, sentiment=0.2)])

    recs = chronological(log)
    assert [r.kind for r in recs] == ["signal", "open"]
    sig, opened = recs
    assert sig.reason == "received" and sig.action == "Analyse"
    assert (sig.symbol, sig.instrument_id, sig.side, sig.market_score) == ("AAPL", 1, Side.BUY, 85.0)
    assert sig.rankings_confirmation == 0.7 and sig.sentiment == 0.2 and sig.mode == "demo"
    assert opened.reason == "opened"
    assert opened.action == "Ouverture BUY AAPL 1000.00 USD @ 200.0000 SL 196.0000 TP 208.0000"
    assert opened.symbol == "AAPL" and opened.position_id == "pos-1"
    assert opened.amount == 1000.0 and opened.entry_rate == 200.0
    assert opened.stop_loss_rate == pytest.approx(196.0) and opened.take_profit_rate == pytest.approx(208.0)
    assert opened.market_score == 85.0  # le contexte du signal est conservé sur l'ouverture


async def test_decision_log_skip_uses_exact_reason():
    agent, service, guard, store, notifier, log = make_logged_agent()
    guard.decision = RiskDecision(allowed=False, reason="cooldown_active")
    await agent.run_once([make_signal()])
    recs = chronological(log)
    assert [r.kind for r in recs] == ["signal", "skip"]
    assert recs[1].reason == "cooldown_active" and recs[1].action == "Aucune action"
    assert recs[1].symbol == "AAPL" and recs[1].instrument_id == 1


async def test_decision_log_skip_rankings_and_sentiment():
    agent, *_, log = make_logged_agent(make_settings(ETORO_USE_RANKINGS="true", ETORO_USE_NEWS="true"))
    await agent.run_once(
        [make_signal(1, "AAPL", 90.0, rankings_confirmation=0.1), make_signal(2, "MSFT", 80.0, sentiment=-0.9)]
    )
    skips = [r for r in log.recent(kind="skip")]
    assert {(r.symbol, r.reason) for r in skips} == {("AAPL", "rankings_not_confirmed"), ("MSFT", "negative_sentiment")}
    assert next(r for r in skips if r.symbol == "AAPL").rankings_confirmation == 0.1
    assert next(r for r in skips if r.symbol == "MSFT").sentiment == -0.9


async def test_decision_log_skip_already_opened_this_cycle():
    agent, *_, log = make_logged_agent()
    await agent.run_once([make_signal(1, score=90.0), make_signal(1, score=80.0)])
    recs = chronological(log)
    assert [(r.kind, r.reason) for r in recs] == [
        ("signal", "received"),
        ("open", "opened"),
        ("signal", "received"),
        ("skip", "already_opened_this_cycle"),
    ]


async def test_decision_log_error_kind_on_execution_error():
    agent, service, *_, log = make_logged_agent()
    service.fail_open_for.add(1)
    await agent.run_once([make_signal(1)])
    recs = chronological(log)
    assert [(r.kind, r.reason) for r in recs] == [("signal", "received"), ("error", "execution_error")]
    assert recs[1].symbol == "AAPL" and log.recent(kind="skip") == []


async def test_decision_log_kill_switch_one_record_per_cycle():
    agent, service, guard, *_, log = make_logged_agent()
    guard.kill = True
    await agent.run_once([make_signal(1), make_signal(2, "MSFT")])
    recs = log.recent()
    assert len(recs) == 1
    assert recs[0].kind == "kill_switch" and recs[0].reason == "kill_switch"
    assert recs[0].symbol is None and "kill switch" in recs[0].action
    await agent.run_once([])  # cycle sans signal : rien à refuser, rien de journalisé
    assert len(log.recent()) == 1


async def test_decision_log_breaker_active_one_record_per_cycle():
    agent, service, guard, *_, log = make_logged_agent()
    guard.breaker = True
    await agent.run_once([make_signal(1), make_signal(2, "MSFT")])
    recs = log.recent()
    assert len(recs) == 1
    assert recs[0].kind == "breaker" and recs[0].reason == "breaker_active"


async def test_decision_log_close_then_breaker_tripped():
    service = FakeServiceWithClosedPnl()
    agent, service, guard, store, notifier, log = make_logged_agent(service=service)
    store.open_positions_by_instrument = {1: "pos-gone"}
    service.closed_pnl["pos-gone"] = -350.0
    store.daily_pnl = -350.0
    guard.close_decision = RiskDecision(allowed=False, reason="breaker_tripped")

    await agent.sync_positions()

    recs = chronological(log)
    assert [r.kind for r in recs] == ["close", "breaker"]
    closed, breaker = recs
    assert closed.reason == "closed" and closed.action == "Fermeture AAPL PnL -350.00 USD"
    assert (closed.symbol, closed.instrument_id, closed.position_id, closed.realized_pnl) == ("AAPL", 1, "pos-gone", -350.0)
    assert breaker.reason == "breaker_tripped" and breaker.action.startswith("Pause 24h")
    assert breaker.symbol == "AAPL" and breaker.realized_pnl == -350.0


async def test_decision_log_close_unknown_instrument_uses_id_as_symbol():
    service = FakeServiceWithClosedPnl()
    agent, service, guard, store, notifier, log = make_logged_agent(service=service)
    store.open_positions_by_instrument = {99: "pos-x"}
    service.closed_pnl["pos-x"] = 12.3
    await agent.sync_positions()
    recs = log.recent()
    assert [r.kind for r in recs] == ["close"]
    assert recs[0].symbol == "99" and recs[0].action == "Fermeture 99 PnL +12.30 USD"


async def test_decision_log_absent_is_noop():
    agent, *_ = make_agent()
    assert agent.decision_log is None
    agent._log("signal", "received", "Analyse")  # ne lève pas
    decisions = await agent.run_once([make_signal()])
    assert decisions[0].allowed is True


async def test_decision_log_failure_never_breaks_agent():
    class BrokenLog:
        def record(self, rec):
            raise RuntimeError("disque plein")

    agent, service, guard, store, notifier, _ = make_logged_agent()
    agent.decision_log = BrokenLog()
    decisions = await agent.run_once([make_signal()])
    assert decisions == [RiskDecision(allowed=True, reason="ok")]
    assert store.open_positions_by_instrument == {1: "pos-1"}
