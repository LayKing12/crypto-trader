"""Types partagés du module eToro de CryptoMind."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Instrument(BaseModel):
    symbol: str  # ex: "AAPL", "XAUUSD"
    instrument_id: int  # id eToro
    display_name: str = ""
    asset_class: str = "stock"  # "stock" | "commodity" | "index" | "etf"


class Quote(BaseModel):
    instrument_id: int
    bid: float
    ask: float
    timestamp: datetime


class Candle(BaseModel):
    instrument_id: int
    from_date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class OrderRequest(BaseModel):
    instrument_id: int
    side: Side
    amount: float = Field(gt=0)  # montant en devise du compte (USD), pas en unités
    leverage: int = Field(default=1, ge=1, le=5)
    entry_rate: float = Field(gt=0)  # prix d'entrée estimé (ask si BUY, bid si SELL)
    stop_loss_rate: float | None = None  # OBLIGATOIRE : RiskGuard refuse si None
    take_profit_rate: float | None = None  # OBLIGATOIRE : RiskGuard refuse si None
    score: float = Field(ge=0, le=100)  # market_score ayant déclenché l'ordre


class Position(BaseModel):
    position_id: str
    instrument_id: int
    side: Side
    amount: float
    open_rate: float
    stop_loss_rate: float | None = None
    take_profit_rate: float | None = None
    opened_at: datetime
    unrealized_pnl: float = 0.0


class ClosedPosition(BaseModel):
    position_id: str
    instrument_id: int
    realized_pnl: float
    closed_at: datetime


class Signal(BaseModel):
    instrument_id: int
    symbol: str
    side: Side
    market_score: float = Field(ge=0, le=100)  # produit par indicator_engine côté CryptoMind
    rankings_confirmation: float | None = None  # 0..1, produit par rankings.py
    sentiment: float | None = None  # -1..1, produit par news_feed.py
    generated_at: datetime


class RiskDecision(BaseModel):
    allowed: bool
    reason: str  # snake_case stable : "ok", "kill_switch", "breaker_active", ...


class DailyStats(BaseModel):
    date: str  # YYYY-MM-DD
    trades_opened: int = 0
    trades_closed: int = 0
    realized_pnl: float = 0.0
    realized_pnl_pct: float = 0.0
    open_positions: int = 0
    breaker_active: bool = False
    kill_switch: bool = False
    mode: str = "demo"


class DecisionRecord(BaseModel):
    """Une décision de l'Agent Portfolio eToro : signal, refus, ouverture, fermeture, breaker..."""

    id: str
    at: datetime
    kind: Literal["signal", "open", "close", "skip", "breaker", "kill_switch", "error", "sync"]
    symbol: str | None = None
    instrument_id: int | None = None
    side: Side | None = None
    market_score: float | None = None
    rankings_confirmation: float | None = None
    sentiment: float | None = None
    reason: str
    action: str
    position_id: str | None = None
    amount: float | None = None
    entry_rate: float | None = None
    stop_loss_rate: float | None = None
    take_profit_rate: float | None = None
    realized_pnl: float | None = None
    mode: str = "demo"
