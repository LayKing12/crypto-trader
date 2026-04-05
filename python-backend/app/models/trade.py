import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)

    # Entry / Exit
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_structure: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Sizing
    position_size_pct: Mapped[float] = mapped_column(Float, nullable=False)
    position_size_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # P&L
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Context at entry
    regime_at_entry: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence_at_entry: Mapped[float] = mapped_column(Float, nullable=False)
    risk_at_entry: Mapped[float] = mapped_column(Float, nullable=False)

    # Result
    result: Mapped[str | None] = mapped_column(String(10), nullable=True)  # win/loss/open

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
