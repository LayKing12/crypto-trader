import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class SignalGenerated(Base):
    __tablename__ = "signals_generated"

    id: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, primary_key=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Scores (0–100)
    market_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Decision
    suggested_bias: Mapped[str] = mapped_column(String(10), nullable=False)  # long/short/neutral
    final_decision: Mapped[str] = mapped_column(String(20), nullable=False)  # execute/skip/disabled
    position_size_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # Claude strategic layer
    claude_analysis_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
