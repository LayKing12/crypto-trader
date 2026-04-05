import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PerformanceRecord(Base):
    __tablename__ = "performance_records"

    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_trades: Mapped[int] = mapped_column(Integer, default=0)
    loss_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)

    total_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)

    current_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_ever: Mapped[float] = mapped_column(Float, default=0.0)

    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_wins: Mapped[int] = mapped_column(Integer, default=0)

    current_base_size_pct: Mapped[float] = mapped_column(Float, default=2.0)
    trading_enabled: Mapped[bool] = mapped_column(default=True)

    capital_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cagnotte_usd: Mapped[float] = mapped_column(Float, default=0.0)  # stablecoin savings
