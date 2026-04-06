"""
CryptoMind — FastAPI Risk Engine V1
Entry point. All routes defined here.
"""
from __future__ import annotations
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.config import get_settings
from app.database import get_db, init_db, AsyncSessionLocal
from app.models import MarketSnapshot, SignalGenerated, Trade
from app.services import (
    indicator_engine,
    scoring_engine,
    risk_engine,
    claude_service,
    paper_trading_engine,
    execution_service,
    performance_tracker,
    market_data_service,
    whatsapp_service,
)
from app.utils.logging_utils import configure_logging, get_logger, log_decision

configure_logging()
log = get_logger("main")
settings = get_settings()

# ── Configuration trading ──────────────────────────────────────────────────
AUTO_TRADE_INTERVAL = 5 * 60  # 5 minutes pour l'entraînement

WATCHED_PAIRS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOTUSD", "LINKUSD", "AVAXUSD", "ATOMUSD", "NEARUSD",
    "ALGOUSD", "LTCUSD",
]

# ── État global du bot ─────────────────────────────────────────────────────
_bot_task: asyncio.Task | None = None
_bot_running: bool = False


# ── Filtre BTC dominant ────────────────────────────────────────────────────

async def get_btc_market_regime() -> str:
    """
    Analyse le BTC pour déterminer le régime global du marché.
    Retourne: 'bull', 'bear', 'neutral'
    """
    try:
        btc_price = await market_data_service.get_price("BTCUSD")
        candles = await market_data_service.get_klines("BTCUSD", "15m")
        if len(candles) < 10:
            return "neutral"

        closes = [c["close"] for c in candles[-20:]]
        if len(closes) < 2:
            return "neutral"

        # Variation BTC sur 1h (4 bougies de 15min)
        recent = closes[-1]
        hour_ago = closes[-5] if len(closes) >= 5 else closes[0]
        btc_change_1h = (recent - hour_ago) / hour_ago * 100

        if btc_change_1h > 2.0:
            log.info("btc_regime", regime="bull", change_1h=round(btc_change_1h, 2))
            return "bull"
        elif btc_change_1h < -2.0:
            log.info("btc_regime", regime="bear", change_1h=round(btc_change_1h, 2))
            return "bear"
        return "neutral"
    except Exception as e:
        log.error("btc_regime_error", error=str(e))
        return "neutral"


# ── Pipeline d'analyse ─────────────────────────────────────────────────────

async def _run_analysis(
    symbol: str,
    req: "AnalyzeRequest",
    db: AsyncSession,
    btc_regime: str = "neutral"
) -> dict:
    """Pipeline d'analyse complet avec filtre BTC dominant."""
    sym = symbol.upper()

    candles = await market_data_service.get_klines(sym, req.interval)
    if len(candles) < 30:
        raise ValueError(f"Not enough candle data for {sym}")

    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]

    fear_greed = await market_data_service.get_fear_greed_index()
    ind = indicator_engine.compute_indicators(sym, closes, highs, lows, volumes)
    scores = scoring_engine.compute_scores(
        ind,
        whale_score=req.whale_score,
        sentiment_score=req.sentiment_score,
        oi_delta_pct=req.oi_delta_pct,
        funding_rate=req.funding_rate,
        fear_greed_index=fear_greed,
    )
    state = await performance_tracker.compute_current_state(db)
    assessment = risk_engine.assess(
        market_score=scores.market_score,
        confluence_count=scores.confluence_count,
        volatility_30d=ind.volatility_30d,
        state=state,
    )

    # ── Biais technique avec filtre BTC ──
    btc_boost = 5.0 if btc_regime == "bull" else -5.0 if btc_regime == "bear" else 0.0
    adjusted_score = scores.market_score + btc_boost

    technical_bias = "neutral"
    if adjusted_score > 55:
        technical_bias = "long"
    elif adjusted_score < 45:
        technical_bias = "short"

    # ── Mode défensif si BTC en bear fort ──
    if btc_regime == "bear" and sym != "BTCUSD":
        if technical_bias == "long":
            technical_bias = "neutral"
            log.info("btc_filter_blocked_long", symbol=sym)

    claude_result = {}
    execute_ok = True
    claude_reason = "no_claude"
    if technical_bias != "neutral" and assessment.trading_enabled:
        claude_result = await claude_service.analyze(
            symbol=sym,
            regime=ind.regime,
            market_score=scores.market_score,
            confidence_score=assessment.confidence_score,
            risk_score=assessment.risk_score,
            rsi=ind.rsi,
            trend_score=scores.trend_score,
            volume_ratio=ind.volume_ratio,
        )
        execute_ok, claude_reason = claude_service.should_execute(technical_bias, claude_result)

    # ── Décision finale — seuil abaissé à 50 ──
    if not assessment.trading_enabled:
        final_decision = "disabled"
    elif technical_bias == "neutral":
        final_decision = "skip_neutral"
    elif not execute_ok:
        final_decision = f"skip_{claude_reason}"
    elif scores.market_score < 50:
        final_decision = "skip_low_score"
    else:
        final_decision = "execute"

    snapshot = MarketSnapshot(
        symbol=sym, price=ind.price, rsi=ind.rsi,
        ema20=ind.ema20, ema50=ind.ema50, ema200=ind.ema200,
        atr=ind.atr, volatility_30d=ind.volatility_30d,
        fear_greed_index=fear_greed, whale_score=req.whale_score,
        sentiment_score=req.sentiment_score, funding_rate=req.funding_rate,
        oi_delta_pct=req.oi_delta_pct, regime=ind.regime,
    )
    db.add(snapshot)

    signal = SignalGenerated(
        symbol=sym,
        market_score=scores.market_score,
        confidence_score=assessment.confidence_score,
        risk_score=assessment.risk_score,
        suggested_bias=technical_bias,
        final_decision=final_decision,
        position_size_pct=assessment.position_size_pct,
        claude_analysis_json=claude_result,
    )
    db.add(signal)
    await db.flush()

    log_decision(
        log, sym,
        inputs={"rsi": ind.rsi, "regime": ind.regime, "price": ind.price, "btc_regime": btc_regime},
        scores={
            "market_score": scores.market_score,
            "adjusted_score": adjusted_score,
            "confidence_score": assessment.confidence_score,
            "risk_score": assessment.risk_score,
        },
        claude_output=claude_result,
        decision=final_decision,
    )

    return {
        "symbol": sym, "price": ind.price, "regime": ind.regime,
        "rsi": ind.rsi, "ema20": ind.ema20, "ema50": ind.ema50, "ema200": ind.ema200,
        "btc_regime": btc_regime,
        "scores": {
            "market_score": scores.market_score,
            "adjusted_score": adjusted_score,
            "confidence_score": assessment.confidence_score,
            "risk_score": assessment.risk_score,
            "confluence_count": scores.confluence_count,
        },
        "decision": final_decision,
        "position_size_pct": assessment.position_size_pct,
        "stop_loss_pct": assessment.stop_loss_pct,
        "claude_analysis": claude_result,
        "trading_enabled": assessment.trading_enabled,
    }


# ── Stop-loss checker ──────────────────────────────────────────────────────

async def _check_stop_losses(db: AsyncSession, symbol: str):
    open_trades = await paper_trading_engine.get_open_trades(db)
    for trade in open_trades:
        if trade.symbol != symbol:
            continue
        price = await market_data_service.get_price(symbol)
        if price and await paper_trading_engine.check_stop_loss(price, trade):
            closed = await paper_trading_engine.close_paper_trade(db, trade.id, price)
            whatsapp_service.notify_stop_loss_hit(symbol, price)
            whatsapp_service.notify_trade_closed(
                symbol, closed.pnl_pct, closed.pnl_usd, closed.result
            )
            log.info("stop_loss_triggered", symbol=symbol, price=price)


# ── Boucle automatique 24h/24 ──────────────────────────────────────────────

async def auto_trading_loop():
    global _bot_running
    await asyncio.sleep(30)
    log.info("auto_trading_loop_started", pairs=len(WATCHED_PAIRS), interval_min=AUTO_TRADE_INTERVAL // 60)

    while _bot_running:
        try:
            # 1. Analyse du régime BTC en premier
            btc_regime = await get_btc_market_regime()
            log.info("auto_cycle_start", btc_regime=btc_regime, pairs=len(WATCHED_PAIRS))

            async with AsyncSessionLocal() as db:
                for symbol in WATCHED_PAIRS:
                    if not _bot_running:
                        break
                    try:
                        req = AnalyzeRequest(symbol=symbol, interval="15m")
                        analysis = await _run_analysis(symbol, req, db, btc_regime)

                        if analysis["decision"] == "execute":
                            price      = analysis["price"]
                            size_pct   = analysis["position_size_pct"]
                            confidence = analysis["scores"]["confidence_score"]
                            risk       = analysis["scores"]["risk_score"]
                            regime     = analysis["regime"]

                            if settings.paper_trading:
                                trade = await paper_trading_engine.open_paper_trade(
                                    db, symbol, price, size_pct, confidence, risk, regime
                                )
                                whatsapp_service.notify_trade_opened(
                                    symbol, price, size_pct, trade.stop_loss_price, True
                                )
                                log.info("auto_paper_trade_opened", symbol=symbol, price=price, btc_regime=btc_regime)
                            else:
                                order = await execution_service.execute_trade(
                                    symbol, price, size_pct, analysis["stop_loss_pct"]
                                )
                                actual_price = order["actual_price"]
                                trade = await paper_trading_engine.open_paper_trade(
                                    db, symbol, actual_price, size_pct, confidence, risk, regime
                                )
                                trade.is_paper = False
                                await db.flush()
                                whatsapp_service.notify_trade_opened(
                                    symbol, actual_price, size_pct, order["stop_loss_price"], False
                                )

                        await _check_stop_losses(db, symbol)
                        await asyncio.sleep(1)

                    except Exception as e:
                        log.error("auto_trade_symbol_error", symbol=symbol, error=str(e))
                        continue

                await db.commit()

        except Exception as e:
            log.error("auto_trading_loop_error", error=str(e))

        if _bot_running:
            log.info("auto_cycle_done", next_in_min=AUTO_TRADE_INTERVAL // 60)
            await asyncio.sleep(AUTO_TRADE_INTERVAL)


# ── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task, _bot_running
    await init_db()
    await market_data_service.start_ws_listener()
    _bot_running = True
    _bot_task = asyncio.create_task(auto_trading_loop())
    log.info("cryptomind_started", paper_trading=settings.paper_trading)
    yield
    _bot_running = False
    if _bot_task:
        _bot_task.cancel()
    await execution_service.close_exchange()
    log.info("cryptomind_stopped")


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="CryptoMind Risk Engine", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str
    interval: str = "15m"
    whale_score: float = 50.0
    sentiment_score: float = 50.0
    oi_delta_pct: float | None = None
    funding_rate: float | None = None


class CloseTradeRequest(BaseModel):
    exit_price: float


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "paper_trading": settings.paper_trading,
        "capital_usd": settings.total_capital_usd,
        "watched_pairs": len(WATCHED_PAIRS),
        "auto_trade_interval_min": AUTO_TRADE_INTERVAL // 60,
    }


# ── Bot control ────────────────────────────────────────────────────────────

@app.post("/api/bot/start")
async def bot_start():
    global _bot_task, _bot_running
    if _bot_running:
        return {"running": True, "message": "Bot déjà en marche"}
    _bot_running = True
    _bot_task = asyncio.create_task(auto_trading_loop())
    log.info("bot_started_manually")
    return {"running": True, "message": "Bot démarré"}


@app.post("/api/bot/stop")
async def bot_stop():
    global _bot_task, _bot_running
    _bot_running = False
    if _bot_task:
        _bot_task.cancel()
        _bot_task = None
    log.info("bot_stopped_manually")
    return {"running": False, "message": "Bot arrêté"}


@app.get("/api/bot/status")
async def bot_status():
    return {
        "running": _bot_running,
        "paper_trading": settings.paper_trading,
        "watched_pairs": len(WATCHED_PAIRS),
        "interval_min": AUTO_TRADE_INTERVAL // 60,
    }


# ── Analyze ────────────────────────────────────────────────────────────────

@app.post("/api/analyze/{symbol}")
async def analyze_symbol(
    symbol: str,
    req: AnalyzeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        btc_regime = await get_btc_market_regime()
        return await _run_analysis(symbol, req, db, btc_regime)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── Trading ────────────────────────────────────────────────────────────────

@app.post("/api/trade/{symbol}")
async def open_trade(
    symbol: str,
    req: AnalyzeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        btc_regime = await get_btc_market_regime()
        analysis = await _run_analysis(symbol, req, db, btc_regime)
    except ValueError as e:
        raise HTTPException(422, str(e))

    if analysis["decision"] != "execute":
        return {"status": "skipped", "reason": analysis["decision"], "analysis": analysis}

    sym        = symbol.upper()
    price      = analysis["price"]
    size_pct   = analysis["position_size_pct"]
    confidence = analysis["scores"]["confidence_score"]
    risk       = analysis["scores"]["risk_score"]
    regime     = analysis["regime"]

    if settings.paper_trading:
        trade = await paper_trading_engine.open_paper_trade(
            db, sym, price, size_pct, confidence, risk, regime
        )
        whatsapp_service.notify_trade_opened(sym, price, size_pct, trade.stop_loss_price, True)
        return {"status": "paper_trade_opened", "trade_id": str(trade.id), "analysis": analysis}

    order = await execution_service.execute_trade(sym, price, size_pct, analysis["stop_loss_pct"])
    actual_price = order["actual_price"]
    trade = await paper_trading_engine.open_paper_trade(
        db, sym, actual_price, size_pct, confidence, risk, regime
    )
    trade.is_paper = False
    await db.flush()
    whatsapp_service.notify_trade_opened(sym, actual_price, size_pct, order["stop_loss_price"], False)
    return {"status": "live_trade_opened", "trade_id": str(trade.id), "order": order}


@app.post("/api/trade/{trade_id}/close")
async def close_trade(
    trade_id: str,
    req: CloseTradeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        tid = uuid.UUID(trade_id)
    except ValueError:
        raise HTTPException(400, "Invalid trade_id")

    trade = await paper_trading_engine.close_paper_trade(db, tid, req.exit_price)
    whatsapp_service.notify_trade_closed(trade.symbol, trade.pnl_pct, trade.pnl_usd, trade.result)
    return {"status": "closed", "pnl_pct": trade.pnl_pct, "pnl_usd": trade.pnl_usd, "result": trade.result}


# ── Dashboard ──────────────────────────────────────────────────────────────

@app.get("/api/performance")
async def get_performance(db: Annotated[AsyncSession, Depends(get_db)]):
    state = await performance_tracker.compute_current_state(db)
    snapshot = await performance_tracker.record_snapshot(db, state)
    return {
        "total_trades": snapshot.total_trades,
        "win_rate": snapshot.win_rate,
        "total_pnl_usd": snapshot.total_pnl_usd,
        "total_pnl_pct": snapshot.total_pnl_pct,
        "current_drawdown": snapshot.current_drawdown,
        "capital_usd": snapshot.capital_usd,
        "cagnotte_usd": snapshot.cagnotte_usd,
        "consecutive_losses": snapshot.consecutive_losses,
        "consecutive_wins": snapshot.consecutive_wins,
        "trading_enabled": snapshot.trading_enabled,
        "base_size_pct": snapshot.current_base_size_pct,
        "paper_trading": settings.paper_trading,
    }


@app.get("/api/trades")
async def list_trades(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, le=200),
    result: str | None = Query(None),
):
    try:
        q = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
        if result:
            q = q.where(Trade.result == result)
        rows = await db.execute(q)
        trades = rows.scalars().all()
        return [
            {
                "id": str(t.id),
                "symbol": t.symbol,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss_price": t.stop_loss_price,
                "position_size_usd": t.position_size_usd,
                "position_size_pct": t.position_size_pct,
                "take_profit_structure": t.take_profit_structure,
                "pnl_pct": t.pnl_pct,
                "pnl_usd": t.pnl_usd,
                "result": t.result,
                "regime_at_entry": t.regime_at_entry,
                "confidence_at_entry": t.confidence_at_entry,
                "is_paper": t.is_paper,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in trades
        ]
    except Exception as e:
        log.error("list_trades_error", error=str(e))
        return []


@app.get("/api/price/{symbol}")
async def get_price(symbol: str):
    price = await market_data_service.get_price(symbol.upper())
    if price is None:
        raise HTTPException(404, f"No price data for {symbol}")
    return {"symbol": symbol.upper(), "price": price}


@app.get("/api/signals")
async def list_signals(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, le=100),
):
    result = await db.execute(
        select(SignalGenerated).order_by(desc(SignalGenerated.timestamp)).limit(limit)
    )
    signals = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "symbol": s.symbol,
            "timestamp": s.timestamp.isoformat(),
            "market_score": s.market_score,
            "confidence_score": s.confidence_score,
            "risk_score": s.risk_score,
            "suggested_bias": s.suggested_bias,
            "final_decision": s.final_decision,
            "position_size_pct": s.position_size_pct,
            "claude_analysis": s.claude_analysis_json,
        }
        for s in signals
    ]


@app.get("/api/market/regime")
async def market_regime():
    """Retourne le régime BTC actuel — utile pour le dashboard."""
    regime = await get_btc_market_regime()
    return {"btc_regime": regime}


# ── Emergency ──────────────────────────────────────────────────────────────

@app.post("/api/panic")
async def panic_button(db: Annotated[AsyncSession, Depends(get_db)]):
    global _bot_running, _bot_task
    _bot_running = False
    if _bot_task:
        _bot_task.cancel()
        _bot_task = None

    open_trades = await paper_trading_engine.get_open_trades(db)
    closed = []
    for trade in open_trades:
        if trade.is_paper:
            price = await market_data_service.get_price(trade.symbol) or trade.entry_price
            closed_trade = await paper_trading_engine.close_paper_trade(db, trade.id, price)
            closed.append({
                "id": str(trade.id),
                "symbol": trade.symbol,
                "pnl_pct": closed_trade.pnl_pct,
            })

    whatsapp_service.notify_risk_alert(f"🚨 PANIC: {len(closed)} trades fermés — Bot arrêté")
    live_open = [t for t in open_trades if not t.is_paper]

    return {
        "bot_stopped": True,
        "paper_closed": closed,
        "live_trades_requiring_manual_close": [
            {"id": str(t.id), "symbol": t.symbol, "entry_price": t.entry_price}
            for t in live_open
        ],
    }