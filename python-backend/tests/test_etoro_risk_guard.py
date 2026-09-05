"""Tests des garde-fous RiskGuard : une cause de refus a la fois."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from etoro.config import Settings
from etoro.models import ClosedPosition, OrderRequest, Position, Side, Signal
from etoro.risk_guard import RiskGuard
from etoro.state_store import StateStore

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
EQUITY = 10_000.0
AAPL_ID = 1001
MSFT_ID = 1002
NVDA_ID = 1003
AMZN_ID = 1004


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        ETORO_API_KEY="test",
        ETORO_STATE_PATH=str(tmp_path / "s.json"),
        ETORO_MODE="demo",
        ETORO_CREDENTIALS_ROTATED=False,
        ETORO_AGENT_ENABLED=True,
        ETORO_MAX_OPEN_POSITIONS=3,
        ETORO_COOLDOWN_HOURS=4.0,
        ETORO_DAILY_LOSS_LIMIT_PCT=3.0,
        ETORO_BREAKER_PAUSE_HOURS=24.0,
        ETORO_MIN_SCORE=70.0,
        ETORO_POSITION_SIZE_PCT=10.0,
        ETORO_MAX_LEVERAGE=1,
        ETORO_UNIVERSE="AAPL,MSFT,NVDA,AMZN",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_signal(instrument_id=AAPL_ID, symbol="AAPL", side=Side.BUY, score=85.0) -> Signal:
    return Signal(
        instrument_id=instrument_id, symbol=symbol, side=side, market_score=score, generated_at=NOW
    )


def make_order(instrument_id=AAPL_ID, side=Side.BUY, amount=1_000.0, leverage=1,
               entry=100.0, sl=98.0, tp=104.0, score=85.0) -> OrderRequest:
    return OrderRequest(
        instrument_id=instrument_id, side=side, amount=amount, leverage=leverage,
        entry_rate=entry, stop_loss_rate=sl, take_profit_rate=tp, score=score,
    )


def make_position(position_id: str, instrument_id: int) -> Position:
    return Position(
        position_id=position_id, instrument_id=instrument_id, side=Side.BUY,
        amount=1_000.0, open_rate=100.0, opened_at=NOW - timedelta(hours=1),
    )


@pytest.fixture
def store(tmp_path) -> StateStore:
    s = StateStore(str(tmp_path / "s.json"))
    s.reset_day(equity=EQUITY, today=NOW.date())
    return s


@pytest.fixture
def guard(tmp_path, store) -> RiskGuard:
    return RiskGuard(make_settings(tmp_path), store)


# ------------------------------------------------------------------ cas ok


def test_ok_buy(guard):
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert d.allowed is True and d.reason == "ok"


def test_ok_sell(guard):
    order = make_order(side=Side.SELL, entry=100.0, sl=102.0, tp=96.0)
    d = guard.check_open(make_signal(side=Side.SELL), order, [], EQUITY, NOW)
    assert d.allowed is True and d.reason == "ok"


def test_check_open_does_not_mutate_store(guard, store):
    guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert store.open_positions_by_instrument == {} and store.last_trade_at == {}


# ------------------------------------------------------- refus, un par un


def test_kill_switch_from_store(guard, store):
    store.set_kill_switch(True, "ops")
    assert guard.kill_switch_active() is True
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "kill_switch")


def test_kill_switch_from_settings(tmp_path, store):
    guard = RiskGuard(make_settings(tmp_path, ETORO_AGENT_ENABLED=False), store)
    assert guard.kill_switch_active() is True
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "kill_switch")


def test_breaker_active(guard, store):
    store.breaker_until = NOW + timedelta(hours=1)
    assert guard.is_breaker_active(NOW) is True
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "breaker_active")


def test_real_mode_without_rotation(tmp_path, store):
    guard = RiskGuard(make_settings(tmp_path, ETORO_MODE="real", ETORO_CREDENTIALS_ROTATED=False), store)
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "real_mode_without_rotation")


def test_real_mode_with_rotation_is_allowed(tmp_path, store):
    guard = RiskGuard(make_settings(tmp_path, ETORO_MODE="real", ETORO_CREDENTIALS_ROTATED=True), store)
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert d.reason == "ok"


def test_score_below_min(guard):
    d = guard.check_open(make_signal(score=69.9), make_order(score=69.9), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "score_below_min")


def test_score_exactly_min_is_allowed(guard):
    d = guard.check_open(make_signal(score=70.0), make_order(score=70.0), [], EQUITY, NOW)
    assert d.reason == "ok"


@pytest.mark.parametrize("sl,tp", [(None, 104.0), (98.0, None), (None, None)])
def test_missing_sl_tp(guard, sl, tp):
    d = guard.check_open(make_signal(), make_order(sl=sl, tp=tp), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "missing_sl_tp")


@pytest.mark.parametrize(
    "side,sl,tp",
    [
        (Side.BUY, 102.0, 104.0),   # SL au-dessus de l'entree
        (Side.BUY, 98.0, 99.0),     # TP sous l'entree
        (Side.BUY, 100.0, 104.0),   # SL egal a l'entree
        (Side.BUY, 104.0, 98.0),    # inverses
        (Side.SELL, 98.0, 96.0),    # SL sous l'entree
        (Side.SELL, 102.0, 101.0),  # TP au-dessus de l'entree
        (Side.SELL, 102.0, 100.0),  # TP egal a l'entree
        (Side.SELL, 96.0, 102.0),   # inverses
    ],
)
def test_invalid_sl_tp(guard, side, sl, tp):
    order = make_order(side=side, entry=100.0, sl=sl, tp=tp)
    d = guard.check_open(make_signal(side=side), order, [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "invalid_sl_tp")


def test_leverage_too_high(guard):
    d = guard.check_open(make_signal(), make_order(leverage=2), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "leverage_too_high")


def test_amount_too_large(guard):
    # 10 % de 10 000 = 1 000 ; tolerance 1 % => 1 010 max.
    d = guard.check_open(make_signal(), make_order(amount=1_010.01), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "amount_too_large")


def test_amount_within_tolerance_is_allowed(guard):
    d = guard.check_open(make_signal(), make_order(amount=1_010.0), [], EQUITY, NOW)
    assert d.reason == "ok"


def test_instrument_not_in_universe(guard):
    signal = make_signal(instrument_id=9999, symbol="TSLA")
    d = guard.check_open(signal, make_order(instrument_id=9999), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "instrument_not_in_universe")


def test_max_positions_reached_from_broker(guard):
    open_positions = [
        make_position("p1", MSFT_ID), make_position("p2", NVDA_ID), make_position("p3", AMZN_ID)
    ]
    d = guard.check_open(make_signal(), make_order(), open_positions, EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "max_positions_reached")


def test_max_positions_reached_from_store(guard, store):
    store.record_open(MSFT_ID, "p1", NOW - timedelta(days=1))
    store.record_open(NVDA_ID, "p2", NOW - timedelta(days=1))
    store.record_open(AMZN_ID, "p3", NOW - timedelta(days=1))
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "max_positions_reached")


def test_max_positions_counts_union_without_double_counting(guard, store):
    store.record_open(MSFT_ID, "p1", NOW - timedelta(days=1))
    store.record_open(NVDA_ID, "p2", NOW - timedelta(days=1))
    # Les memes positions vues cote broker ne comptent pas deux fois : 2 < 3 => ok.
    open_positions = [make_position("p1", MSFT_ID), make_position("p2", NVDA_ID)]
    d = guard.check_open(make_signal(), make_order(), open_positions, EQUITY, NOW)
    assert d.reason == "ok"


def test_position_already_open_from_store(guard, store):
    store.record_open(AAPL_ID, "p1", NOW - timedelta(days=1))
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "position_already_open")


def test_position_already_open_from_broker(guard):
    d = guard.check_open(make_signal(), make_order(), [make_position("p1", AAPL_ID)], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "position_already_open")


def test_cooldown_active_after_close(guard, store):
    store.record_close(AAPL_ID, NOW - timedelta(hours=3, minutes=59))
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert (d.allowed, d.reason) == (False, "cooldown_active")


def test_cooldown_elapsed_is_allowed(guard, store):
    store.record_close(AAPL_ID, NOW - timedelta(hours=4))
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert d.reason == "ok"


def test_cooldown_zero_disables_rule(tmp_path, store):
    guard = RiskGuard(make_settings(tmp_path, ETORO_COOLDOWN_HOURS=0), store)
    store.record_close(AAPL_ID, NOW)
    d = guard.check_open(make_signal(), make_order(), [], EQUITY, NOW)
    assert d.reason == "ok"


def test_rules_order_kill_switch_wins_over_everything(guard, store):
    store.set_kill_switch(True, "ops")
    store.breaker_until = NOW + timedelta(hours=1)
    d = guard.check_open(make_signal(score=10.0), make_order(sl=None, tp=None, score=10.0), [], EQUITY, NOW)
    assert d.reason == "kill_switch"


def test_rules_order_breaker_before_score(guard, store):
    store.breaker_until = NOW + timedelta(hours=1)
    d = guard.check_open(make_signal(score=10.0), make_order(score=10.0), [], EQUITY, NOW)
    assert d.reason == "breaker_active"


# ---------------------------------------------------------- circuit breaker


def test_breaker_trips_at_exactly_limit(guard, store):
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=-300.0, closed_at=NOW)
    d = guard.on_position_closed(closed, NOW)
    assert (d.allowed, d.reason) == (False, "breaker_tripped")
    assert store.daily_pnl == -300.0
    assert store.breaker_until == NOW + timedelta(hours=24)
    assert guard.is_breaker_active(NOW) is True
    # Ouverture refusee tant que le breaker est actif...
    d = guard.check_open(make_signal(instrument_id=MSFT_ID, symbol="MSFT"),
                         make_order(instrument_id=MSFT_ID), [], EQUITY, NOW + timedelta(hours=23, minutes=59))
    assert d.reason == "breaker_active"
    # ... puis levee apres 24 h.
    after = NOW + timedelta(hours=24)
    assert guard.is_breaker_active(after) is False
    d = guard.check_open(make_signal(instrument_id=MSFT_ID, symbol="MSFT"),
                         make_order(instrument_id=MSFT_ID), [], EQUITY, after)
    assert d.reason == "ok"


def test_breaker_not_tripped_below_limit(guard, store):
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=-299.99, closed_at=NOW)
    d = guard.on_position_closed(closed, NOW)
    assert d.reason == "ok"
    assert store.breaker_until is None
    assert guard.is_breaker_active(NOW) is False


def test_breaker_accumulates_losses(guard, store):
    for pnl in (-100.0, -100.0):
        d = guard.on_position_closed(
            ClosedPosition(position_id="p", instrument_id=AAPL_ID, realized_pnl=pnl, closed_at=NOW), NOW
        )
        assert d.reason == "ok"
    d = guard.on_position_closed(
        ClosedPosition(position_id="p", instrument_id=AAPL_ID, realized_pnl=-100.0, closed_at=NOW), NOW
    )
    assert d.reason == "breaker_tripped"
    assert store.daily_pnl == pytest.approx(-300.0)


def test_profit_never_trips_breaker(guard, store):
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=500.0, closed_at=NOW)
    d = guard.on_position_closed(closed, NOW)
    assert d.reason == "ok" and store.daily_pnl == 500.0 and store.breaker_until is None


def test_on_position_closed_releases_instrument_and_sets_cooldown(guard, store):
    store.record_open(AAPL_ID, "p1", NOW - timedelta(hours=5))
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=10.0, closed_at=NOW)
    guard.on_position_closed(closed, NOW)
    assert AAPL_ID not in store.open_positions_by_instrument
    assert store.last_trade_at[AAPL_ID] == NOW


def test_breaker_state_persisted(guard, store, tmp_path):
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=-300.0, closed_at=NOW)
    guard.on_position_closed(closed, NOW)
    reloaded = StateStore(str(tmp_path / "s.json"))
    reloaded.load()
    assert reloaded.breaker_until == NOW + timedelta(hours=24)
    assert reloaded.daily_pnl == -300.0


def test_reset_day_clears_pnl_but_not_breaker(guard, store):
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=-300.0, closed_at=NOW)
    guard.on_position_closed(closed, NOW)
    tomorrow = NOW + timedelta(hours=12)
    store.reset_day(equity=9_700.0, today=tomorrow.date())
    assert store.daily_pnl == 0.0
    assert store.equity_start_of_day == 9_700.0
    assert guard.is_breaker_active(tomorrow) is True


def test_breaker_without_equity_reference_fails_closed(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))  # reset_day jamais appele
    guard = RiskGuard(make_settings(tmp_path), store)
    closed = ClosedPosition(position_id="p1", instrument_id=AAPL_ID, realized_pnl=-1.0, closed_at=NOW)
    d = guard.on_position_closed(closed, NOW)
    assert d.reason == "breaker_tripped"


def test_now_defaults_to_utc_now(guard):
    d = guard.check_open(make_signal(), make_order(), [], EQUITY)
    assert d.reason == "ok"
    assert guard.is_breaker_active() is False
