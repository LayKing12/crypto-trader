"""
Tests unitaires — Paper Trading Engine
Test: API hors-ligne → pas de crash
Test: Stop-loss calculé correctement
Test: Take-profit structure correcte
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from datetime import datetime, timezone

from app.services.paper_trading_engine import (
    open_paper_trade, close_paper_trade,
    check_stop_loss, TAKE_PROFIT_LEVELS,
)
from app.models.trade import Trade


def make_mock_trade(entry_price: float = 50000.0, size_pct: float = 2.0) -> Trade:
    """Create an in-memory Trade object for testing (no DB)."""
    stop = round(entry_price * (1 - 7 / 100), 4)
    tp_structure = {
        f"+{int(pct)}%": {"sell_pct": ep, "target_price": round(entry_price * (1 + pct / 100), 4)}
        for pct, ep in TAKE_PROFIT_LEVELS
    }
    return Trade(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        is_paper=True,
        entry_price=entry_price,
        stop_loss_price=stop,
        take_profit_structure=tp_structure,
        position_size_pct=size_pct,
        position_size_usd=1000.0 * size_pct / 100,
        confidence_at_entry=70.0,
        risk_at_entry=20.0,
        regime_at_entry="bull_trend",
        result="open",
        opened_at=datetime.now(timezone.utc),
    )


class TestStopLossCalculation:
    def test_stop_loss_exactly_7_pct(self):
        """Test 2: Stop-loss price must be exactly entry * 0.93."""
        entry = 50_000.0
        expected_sl = round(entry * 0.93, 4)
        trade = make_mock_trade(entry)
        assert trade.stop_loss_price == expected_sl

    def test_stop_loss_triggered_below(self):
        """check_stop_loss returns True when price ≤ stop_loss_price."""
        import asyncio
        trade = make_mock_trade(50_000.0)
        # Price below stop-loss
        hit = asyncio.get_event_loop().run_until_complete(
            check_stop_loss(46_000.0, trade)
        )
        assert hit is True

    def test_stop_loss_not_triggered_above(self):
        import asyncio
        trade = make_mock_trade(50_000.0)
        hit = asyncio.get_event_loop().run_until_complete(
            check_stop_loss(48_000.0, trade)
        )
        assert hit is False


class TestTakeProfitStructure:
    def test_tp_levels_present(self):
        trade = make_mock_trade(40_000.0)
        assert "+50%" in trade.take_profit_structure
        assert "+100%" in trade.take_profit_structure
        assert "+150%" in trade.take_profit_structure
        assert "+200%" in trade.take_profit_structure

    def test_tp_sell_pcts_sum_to_90(self):
        """10+20+20+40 = 90% (not 100 because partial exits)."""
        trade = make_mock_trade()
        total = sum(v["sell_pct"] for v in trade.take_profit_structure.values())
        assert abs(total - 0.90) < 0.001

    def test_tp_price_at_50_pct(self):
        entry = 40_000.0
        trade = make_mock_trade(entry)
        expected = round(entry * 1.5, 4)
        assert trade.take_profit_structure["+50%"]["target_price"] == expected


class TestPnlCalculation:
    def test_win_pnl_positive(self):
        """Closing above entry gives positive P&L."""
        trade = make_mock_trade(50_000.0)
        exit_price = 55_000.0
        pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        assert pnl_pct == pytest.approx(10.0, rel=1e-4)

    def test_loss_pnl_negative(self):
        trade = make_mock_trade(50_000.0)
        exit_price = 46_500.0  # stop-loss price
        pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        assert pnl_pct == pytest.approx(-7.0, rel=1e-2)


class TestKrakenOffline:
    """Test 3: Simulate Kraken API offline — bot must not crash."""

    @pytest.mark.asyncio
    async def test_execution_service_handles_kraken_offline(self):
        """When Kraken is offline, execution raises but doesn't crash the process."""
        with patch("app.services.execution_service._get_exchange") as mock_ex:
            mock_exchange = AsyncMock()
            mock_exchange.fetch_balance.side_effect = Exception("Connection refused — Kraken offline")
            mock_ex.return_value = mock_exchange

            from app.services.execution_service import get_usd_balance
            balance = await get_usd_balance()
            # Must return 0.0, not raise
            assert balance == 0.0

    @pytest.mark.asyncio
    async def test_market_data_handles_binance_offline(self):
        """When Binance klines endpoint is offline, returns empty list gracefully."""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused — Binance offline")

            from app.services.market_data_service import get_klines
            result = await get_klines("BTCUSDT")
            assert result == []
