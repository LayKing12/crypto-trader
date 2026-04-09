"""
Paper Trading Engine — simulates trades in DB without touching Kraken.
Take profits rapides pour l'entraînement.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.trade import Trade
from app.utils.logging_utils import get_logger
from app.config import get_settings

log = get_logger(__name__)
settings = get_settings()

# ── Take profits rapides pour générer plus de trades ──
TAKE_PROFIT_LEVELS = [
    (3.0,  0.25),   # +3%  → vend 25%
    (5.0,  0.25),   # +5%  → vend 25%
    (8.0,  0.25),   # +8%  → vend 25%
    (15.0, 0.25),   # +15% → vend 25%
]

# Stop-loss serré pour protéger le capital
STOP_LOSS_PCT = 2.0


async def open_paper_trade(
    db: AsyncSession,
    symbol: str,
    entry_price: float,
    position_size_pct: float,
    confidence_at_entry: float,
    risk_at_entry: float,
    regime_at_entry: str,
) -> Trade:
    stop_loss_price = round(entry_price * (1 - STOP_LOSS_PCT / 100), 4)
    position_size_usd = settings.total_capital_usd * position_size_pct / 100

    take_profit_structure = {
        f"+{pct}%": {
            "sell_pct": exit_pct,
            "target_price": round(entry_price * (1 + pct / 100), 4)
        }
        for pct, exit_pct in TAKE_PROFIT_LEVELS
    }

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
        stop_loss=stop_loss_price,
        size_pct=position_size_pct,
        size_usd=position_size_usd,
        take_profits=[f"+{p}%" for p, _ in TAKE_PROFIT_LEVELS],
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

    log.info(
        "paper_trade_closed",
        symbol=trade.symbol,
        pnl_pct=pnl_pct,
        pnl_usd=pnl_usd,
        result=trade.result,
    )

    return trade


async def check_stop_loss(current_price: float, trade: Trade) -> bool:
    """Vérifie si le stop-loss est atteint."""
    return current_price <= trade.stop_loss_price


async def check_take_profit(current_price: float, trade: Trade) -> bool:
    """Vérifie si le premier take profit est atteint."""
    if not trade.take_profit_structure:
        return False
    first_tp = min(
        [level["target_price"] for level in trade.take_profit_structure.values()],
        default=None
    )
    if first_tp and current_price >= first_tp:
        return True
    return False


async def get_open_trades(db: AsyncSession) -> list[Trade]:
    result = await db.execute(select(Trade).where(Trade.result == "open"))
    return list(result.scalars().all())