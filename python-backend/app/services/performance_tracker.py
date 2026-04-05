"""
Performance Tracker — maintains running stats and adaptive state.
Updates RiskState after each trade close.
"""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.trade import Trade
from app.models.performance import PerformanceRecord
from app.services.risk_engine import RiskState
from app.utils.logging_utils import get_logger
from app.config import get_settings
import uuid
from datetime import datetime, timezone

log = get_logger(__name__)
settings = get_settings()


async def compute_current_state(db: AsyncSession) -> RiskState:
    """Rebuild RiskState from trade history."""
    result = await db.execute(
        select(Trade)
        .where(Trade.result.in_(["win", "loss"]))
        .order_by(Trade.closed_at.desc())
        .limit(10)
    )
    recent_trades = list(result.scalars().all())

    # Consecutive losses/wins from most recent
    consecutive_losses = 0
    consecutive_wins = 0
    for t in recent_trades:
        if t.result == "loss":
            if consecutive_wins == 0:
                consecutive_losses += 1
            else:
                break
        elif t.result == "win":
            if consecutive_losses == 0:
                consecutive_wins += 1
            else:
                break

    # Total P&L for drawdown calculation
    total_pnl_result = await db.execute(
        select(func.sum(Trade.pnl_usd)).where(Trade.result.in_(["win", "loss"]))
    )
    total_pnl = float(total_pnl_result.scalar() or 0.0)
    current_drawdown = max(0.0, -total_pnl / settings.total_capital_usd * 100)

    state = RiskState(
        consecutive_losses=consecutive_losses,
        consecutive_wins=consecutive_wins,
        current_drawdown_pct=round(current_drawdown, 2),
        trading_enabled=current_drawdown < settings.drawdown_disable_pct,
        current_base_size_pct=1.0 if consecutive_losses >= settings.max_consecutive_losses else 2.0,
    )

    log.info("risk_state_computed", **state.__dict__)
    return state


async def record_snapshot(db: AsyncSession, state: RiskState) -> PerformanceRecord:
    """Save a performance snapshot to DB."""
    result = await db.execute(
        select(
            func.count(Trade.id).label("total"),
            func.sum(Trade.pnl_usd).label("total_pnl"),
        ).where(Trade.result.in_(["win", "loss"]))
    )
    row = result.one()
    total = int(row.total or 0)
    total_pnl = float(row.total_pnl or 0.0)

    wins_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.result == "win")
    )
    wins = int(wins_result.scalar() or 0)
    win_rate = round(wins / total * 100, 2) if total > 0 else 0.0

    record = PerformanceRecord(
        id=uuid.uuid4(),
        recorded_at=datetime.now(timezone.utc),
        total_trades=total,
        win_trades=wins,
        loss_trades=total - wins,
        win_rate=win_rate,
        total_pnl_usd=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl / settings.total_capital_usd * 100, 2),
        current_drawdown=state.current_drawdown_pct,
        consecutive_losses=state.consecutive_losses,
        consecutive_wins=state.consecutive_wins,
        current_base_size_pct=state.current_base_size_pct,
        trading_enabled=state.trading_enabled,
        capital_usd=settings.total_capital_usd + total_pnl,
        cagnotte_usd=round(max(total_pnl, 0) * 0.20, 2),  # 20% profits sécurisés
    )
    db.add(record)
    await db.flush()
    return record
