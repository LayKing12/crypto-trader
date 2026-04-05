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
    blockchain_service,
)
from app.utils.logging_utils import configure_logging, get_logger, log_decision

configure_logging()
log = get_logger("main")
settings = get_settings()

# ── Paires surveillées 24h/24 ──────────────────────────────────────────────
# FTMUSD, SANDUSD, MANAUSD, APEUSD retirées : données OHLC insuffisantes sur Kraken
WATCHED_PAIRS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOTUSD", "MATICUSD", "LINKUSD", "AVAXUSD", "ATOMUSD",
    "NEARUSD", "ALGOUSD",
]

AUTO_TRADE_INTERVAL = 10 * 60  # 10 minutes

# ── État global du bot ─────────────────────────────────────────────────────
_bot_task: asyncio.Task | None = None
_bot_running: bool = False


# ── Pipeline d'analyse ─────────────────────────────────────────────────────

async def _run_analysis(symbol: str, req: "AnalyzeRequest", db: AsyncSession, save_to_db: bool = True) -> dict:
    """
    Pipeline d'analyse complet.
    save_to_db=False dans la boucle auto pour économiser les requêtes Supabase :
    on n'écrit en DB que les trades exécutés (sinon ~52k insertions/mois).
    """
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

    # Whale score on-chain (blockchain_service) si BTC/ETH, sinon valeur manuelle
    # La requête est mise en cache 5 min → pas de surcharge API
    effective_whale_score = req.whale_score
    if req.whale_score == 50.0:  # valeur par défaut → enrichir avec données on-chain
        effective_whale_score = await blockchain_service.get_whale_score(sym)

    scores = scoring_engine.compute_scores(
        ind,
        whale_score=effective_whale_score,
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

    technical_bias = "neutral"
    if scores.market_score > 65 and ind.regime == "bull_trend":
        technical_bias = "long"
    elif scores.market_score < 35 or ind.regime == "bear_trend":
        technical_bias = "short"

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

    if not assessment.trading_enabled:
        final_decision = "disabled"
    elif technical_bias == "neutral":
        final_decision = "skip_neutral"
    elif not execute_ok:
        final_decision = f"skip_{claude_reason}"
    elif scores.market_score < 62:
        final_decision = "skip_low_score"
    else:
        final_decision = "execute"

    if save_to_db:
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
        inputs={"rsi": ind.rsi, "regime": ind.regime, "price": ind.price},
        scores={
            "market_score": scores.market_score,
            "confidence_score": assessment.confidence_score,
            "risk_score": assessment.risk_score,
        },
        claude_output=claude_result,
        decision=final_decision,
    )

    return {
        "symbol": sym, "price": ind.price, "regime": ind.regime,
        "rsi": ind.rsi, "ema20": ind.ema20, "ema50": ind.ema50, "ema200": ind.ema200,
        "scores": {
            "market_score": scores.market_score,
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
    """Vérifie et ferme les trades dont le stop-loss est atteint."""
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
    """
    Analyse toutes les paires toutes les 10 minutes.
    Bougies 15m pour capturer les mouvements intraday.
    Seuil market_score >= 62 pour réduire les faux signaux.
    DB write uniquement si trade exécuté (économie Supabase : ~50 req/mois vs 52k).
    """
    global _bot_running
    await asyncio.sleep(30)  # Attendre que le serveur soit prêt
    log.info("auto_trading_loop_started", pairs=len(WATCHED_PAIRS), interval_min=AUTO_TRADE_INTERVAL // 60)

    while _bot_running:
        try:
            async with AsyncSessionLocal() as db:
                for symbol in WATCHED_PAIRS:
                    if not _bot_running:
                        break
                    try:
                        # save_to_db=False : analyse en mémoire, 0 insertion Supabase
                        req = AnalyzeRequest(symbol=symbol, interval="15m")
                        analysis = await _run_analysis(symbol, req, db, save_to_db=False)

                        if analysis["decision"] == "execute":
                            price      = analysis["price"]
                            size_pct   = analysis["position_size_pct"]
                            confidence = analysis["scores"]["confidence_score"]
                            risk       = analysis["scores"]["risk_score"]
                            regime     = analysis["regime"]

                            if settings.paper_trading:
                                # Ici on écrit en DB (trade réel)
                                trade = await paper_trading_engine.open_paper_trade(
                                    db, symbol, price, size_pct, confidence, risk, regime
                                )
                                whatsapp_service.notify_trade_opened(
                                    symbol, price, size_pct, trade.stop_loss_price, True
                                )
                                log.info("auto_paper_trade_opened", symbol=symbol, price=price,
                                         score=analysis["scores"]["market_score"])
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
                                log.info("auto_live_trade_opened", symbol=symbol, price=actual_price)

                        await _check_stop_losses(db, symbol)
                        await asyncio.sleep(2)  # 2s entre chaque paire pour ne pas throttler Kraken

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
    # Démarrage automatique du bot au lancement
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

app = FastAPI(
    title="CryptoMind Risk Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic schemas ───────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str
    interval: str = "1h"
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


# ── Core: Analyze & Signal ─────────────────────────────────────────────────

@app.post("/api/analyze/{symbol}")
async def analyze_symbol(
    symbol: str,
    req: AnalyzeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        return await _run_analysis(symbol, req, db)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── Trading ────────────────────────────────────────────────────────────────

@app.post("/api/trade/{symbol}")
async def open_trade(
    symbol: str,
    req: AnalyzeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run full analysis then open a trade if decision == 'execute'."""
    try:
        analysis = await _run_analysis(symbol, req, db)
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


# ── Dashboard data ─────────────────────────────────────────────────────────

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
            "pnl_pct": t.pnl_pct,
            "pnl_usd": t.pnl_usd,
            "result": t.result,
            "regime_at_entry": t.regime_at_entry,
            "confidence_at_entry": t.confidence_at_entry,
            "position_size_pct": t.position_size_pct,
            "is_paper": t.is_paper,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        }
        for t in trades
    ]


@app.get("/api/onchain")
async def get_onchain():
    """Résumé on-chain BTC + ETH pour le dashboard (cache 5 min)."""
    return await blockchain_service.get_onchain_summary()


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


# ── Emergency ──────────────────────────────────────────────────────────────

@app.post("/api/panic")
async def panic_button(db: Annotated[AsyncSession, Depends(get_db)]):
    """PANIC BUTTON — ferme tous les trades ouverts immédiatement."""
    global _bot_running, _bot_task

    # Arrêt du bot
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
        "message": "Bot arrêté. Fermez les positions live manuellement sur Kraken si listées ci-dessus.",
    }