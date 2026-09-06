"""Tests — etoro/notifier.py (Telegram, chat trading, fake injecté)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etoro.config import Settings
from etoro.models import ClosedPosition, DailyStats, Instrument, Position, Side
from etoro.notifier import Notifier


class FakeTelegram:
    def __init__(self, configured=True, fail=False):
        self.configured = configured
        self.fail = fail
        self.calls: list[tuple[str, str, bool]] = []

    async def send_text(self, chat, text, buttons=None, *, priority=False):
        if self.fail:
            raise RuntimeError("telegram down")
        self.calls.append((chat, text, priority))
        return len(self.calls)


def _settings(mode="demo") -> Settings:
    return Settings(ETORO_API_KEY="test", ETORO_TRADING_MODE=mode)


@pytest.mark.asyncio
async def test_noop_when_not_configured():
    tg = FakeTelegram(configured=False)
    n = Notifier(_settings(), telegram=tg)
    assert n.enabled is False
    assert await n.send_text("hello") is False
    assert tg.calls == []


@pytest.mark.asyncio
async def test_position_opened_message_has_prefix_symbol_sl_tp():
    tg = FakeTelegram()
    n = Notifier(_settings(), telegram=tg)
    pos = Position(position_id="p1", instrument_id=1001, side=Side.BUY, amount=100, open_rate=190.12,
                   stop_loss_rate=186.32, take_profit_rate=197.72, opened_at=datetime.now(timezone.utc))
    await n.send_position_opened(pos, Instrument(symbol="AAPL", instrument_id=1001))
    chat, text, priority = tg.calls[0]
    assert chat == "trading" and priority is False
    assert "DEMO" in text and "AAPL" in text and "SL 186.32" in text and "TP 197.72" in text


@pytest.mark.asyncio
async def test_real_prefix_and_priority_events():
    tg = FakeTelegram()
    n = Notifier(_settings("real"), telegram=tg)
    await n.send_breaker_tripped(-3.5)
    await n.send_kill_switch("api", True)
    assert all(c[0] == "trading" and c[2] is True for c in tg.calls)
    assert "REAL" in tg.calls[0][1] and "breaker" in tg.calls[0][1].lower()
    assert "Kill switch ACTIVÉ" in tg.calls[1][1]


@pytest.mark.asyncio
async def test_closed_and_summary_messages():
    tg = FakeTelegram()
    n = Notifier(_settings(), telegram=tg)
    await n.send_position_closed(ClosedPosition(position_id="p1", instrument_id=1001, realized_pnl=12.3,
                                                closed_at=datetime.now(timezone.utc)))
    await n.send_daily_summary(DailyStats(date="2026-09-06", trades_opened=2, trades_closed=1, realized_pnl=-5.0,
                                          realized_pnl_pct=-0.5, open_positions=1, breaker_active=True))
    assert "+12.30 USD" in tg.calls[0][1]
    assert "Résumé 2026-09-06" in tg.calls[1][1] and "breaker ACTIF" in tg.calls[1][1]


@pytest.mark.asyncio
async def test_telegram_failure_never_raises():
    n = Notifier(_settings(), telegram=FakeTelegram(fail=True))
    assert await n.send_text("x") is False
