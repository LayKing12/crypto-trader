"""Tests du Notifier Twilio (client fake, aucun réseau)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etoro.config import Settings
from etoro.models import ClosedPosition, DailyStats, Instrument, Position, Side
from etoro.notifier import MAX_SMS_PER_HOUR, Notifier


class FakeMessages:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("twilio down")
        self.sent.append(kwargs)
        return kwargs


class FakeClient:
    def __init__(self, fail: bool = False):
        self.messages = FakeMessages(fail=fail)


def _settings(**overrides) -> Settings:
    base = dict(
        ETORO_API_KEY="test",
        TWILIO_ACCOUNT_SID="AC123",
        TWILIO_AUTH_TOKEN="tok",
        TWILIO_FROM="+10000000000",
        TWILIO_TO="+33600000000",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _position() -> Position:
    return Position(
        position_id="p1",
        instrument_id=1001,
        side=Side.BUY,
        amount=100,
        open_rate=190.12,
        stop_loss_rate=186.32,
        take_profit_rate=197.72,
        opened_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_noop_sans_config():
    client = FakeClient()
    notifier = Notifier(Settings(_env_file=None, ETORO_API_KEY="test"), client=client)
    assert notifier.enabled is False
    await notifier.send_position_opened(_position(), Instrument(symbol="AAPL", instrument_id=1001))
    assert await notifier.send_text("hello") is False
    assert client.messages.sent == []


@pytest.mark.asyncio
async def test_envoi_ouverture_demo():
    client = FakeClient()
    notifier = Notifier(_settings(), client=client)
    await notifier.send_position_opened(_position(), Instrument(symbol="AAPL", instrument_id=1001))
    assert len(client.messages.sent) == 1
    msg = client.messages.sent[0]
    body = msg["body"]
    assert body.startswith("[CryptoMind eToro DEMO]")
    assert "BUY AAPL" in body
    assert "SL 186.32" in body and "TP 197.72" in body
    assert msg["from_"] == "+10000000000" and msg["to"] == "+33600000000"


@pytest.mark.asyncio
async def test_prefixe_real():
    client = FakeClient()
    notifier = Notifier(_settings(ETORO_MODE="real"), client=client)
    await notifier.send_text("ping")
    assert client.messages.sent[0]["body"].startswith("[CryptoMind eToro REAL]")


@pytest.mark.asyncio
async def test_messages_metier():
    client = FakeClient()
    notifier = Notifier(_settings(), client=client)
    await notifier.send_position_closed(
        ClosedPosition(position_id="p1", instrument_id=1001, realized_pnl=-3.5, closed_at=datetime.now(timezone.utc))
    )
    await notifier.send_daily_summary(DailyStats(date="2026-09-05", trades_opened=2, realized_pnl=12.0, kill_switch=True))
    await notifier.send_breaker_tripped(-3.2)
    await notifier.send_kill_switch("aliou", True)
    bodies = [m["body"] for m in client.messages.sent]
    assert "-3.50" in bodies[0]
    assert "2026-09-05" in bodies[1] and "KILL SWITCH" in bodies[1]
    assert "BREAKER" in bodies[2] and "-3.20%" in bodies[2]
    assert "KILL SWITCH" in bodies[3] and "aliou" in bodies[3]


@pytest.mark.asyncio
async def test_erreur_twilio_ne_leve_pas():
    notifier = Notifier(_settings(), client=FakeClient(fail=True))
    assert await notifier.send_text("boom") is False


@pytest.mark.asyncio
async def test_anti_spam():
    client = FakeClient()
    now = [1000.0]
    notifier = Notifier(_settings(), client=client, clock=lambda: now[0])
    for _ in range(MAX_SMS_PER_HOUR):
        assert await notifier.send_text("x") is True
    # 31e message ordinaire : bloqué
    assert await notifier.send_text("trop") is False
    assert len(client.messages.sent) == MAX_SMS_PER_HOUR
    # breaker / kill switch passent toujours
    await notifier.send_breaker_tripped(-4.0)
    await notifier.send_kill_switch("api", True)
    assert len(client.messages.sent) == MAX_SMS_PER_HOUR + 2
    # une heure plus tard, la fenêtre glissante se libère
    now[0] += 3601
    assert await notifier.send_text("ok") is True
