"""
Paper Trading Engine — simulates trades in DB without touching Kraken.
SL/TP are now per-pair, injected from strategy_engine.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.trade import Trade
from app.utils.logging_utils import get_logger
from app.config import get_settings
from app.services import strategy_engine

log = get_logger(__name__)
settings = get_settings()


def _build_tp_structure(entry_price: float, tp_pct: float) -> dict:
    """
    Build a 2-level take-profit structure from the per-pair TP percentage.
    Level 1: tp_pct        → closes trade (100%)
    Level 2: tp_pct × 2   → backup in case price blows through
    """
    tp1 = round(tp_pct, 1)
    tp2 = round(tp_pct * 2.0, 1)
    return {
        f"+{tp1}%": {
            "sell_pct": 1.0,
            "target_price": round(entry_price * (1 + tp1 / 100), 4),
        },
        f"+{tp2}%": {
            "sell_pct": 1.0,
            "target_price": round(entry_price * (1 + tp2 / 100), 4),
        },
    }


async def open_paper_trade(
    db: AsyncSession,
    symbol: str,
    entry_price: float,
    position_size_pct: float,
    confidence_at_entry: float,
    risk_at_entry: float,
    regime_at_entry: str,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
) -> Trade:
    """
    sl_pct / tp_pct come from strategy_engine.get_pair_config().
    Falls back to defaults if not provided.
    """
    cfg = strategy_engine.get_pair_config(symbol)
    sl_pct = sl_pct if sl_pct is not None else cfg["sl_pct"]
    tp_pct = tp_pct if tp_pct is not None else cfg["tp_pct"]

    stop_loss_price = round(entry_price * (1 - sl_pct / 100), 4)
    position_size_usd = settings.total_capital_usd * position_size_pct / 100
    take_profit_structure = _build_tp_structure(entry_price, tp_pct)

    trade = Trade(
        id=uuid.uuid4(),
        symbol=symbol,
        is_paper=True,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_structure=take_profit_structure,
        position_size_pct=position_size_pct,
        position_size_usd=position_size_usd,
        confidence_at_entry=confidence_at_entry,
        risk_at_entry=risk_at_entry,
        regime_at_entry=regime_at_entry,
        result="open",
        opened_at=datetime.now(timezone.utc),
    )
    db.add(trade)
    await db.flush()

    log.info(
        "paper_trade_opened",
        symbol=symbol,
        entry_price=entry_price,
        sl_pct=sl_pct,
        stop_loss=stop_loss_price,
        tp_pct=tp_pct,
        size_pct=position_size_pct,
        size_usd=position_size_usd,
    )

    return trade


async def close_paper_trade(
    db: AsyncSession,
    trade_id: uuid.UUID,
    exit_price: float,
) -> Trade:
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")

    pnl_pct = round((exit_price - trade.entry_price) / trade.entry_price * 100, 2)
    pnl_usd = round(trade.position_size_usd * pnl_pct / 100, 4)

    trade.exit_price = exit_price
    trade.pnl_pct = pnl_pct
    trade.pnl_usd = pnl_usd
    trade.result = "win" if pnl_pct > 0 else "loss"
    trade.closed_at = datetime.now(timezone.utc)
    await db.flush()  # make result change visible in same session

    # Update discipline tracker
    strategy_engine.on_trade_result(won=(pnl_pct > 0))

    log.info(
        "paper_trade_closed",
        symbol=trade.symbol,
        pnl_pct=pnl_pct,
        pnl_usd=pnl_usd,
        result=trade.result,
    )

    return trade


async def check_stop_loss(current_price: float, trade: Trade) -> bool:
    return current_price <= trade.stop_loss_price


async def check_take_profit(current_price: float, trade: Trade) -> bool:
    """Vérifie si le premier take profit est atteint."""
    if not trade.take_profit_structure:
        return False
    first_tp = min(
        (level["target_price"] for level in trade.take_profit_structure.values()),
        default=None,
    )
    return bool(first_tp and current_price >= first_tp)


async def get_open_trades(db: AsyncSession) -> list[Trade]:
    result = await db.execute(select(Trade).where(Trade.result == "open"))
    return list(result.scalars().all())
