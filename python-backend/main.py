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
from sqlalchemy import select, desc, delete, func

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
    strategy_engine,
    smc_engine,
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
_monitor_task: asyncio.Task | None = None
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

    # ── Per-pair technical entry validation ──
    pair_ok, pair_reason = strategy_engine.validate_pair_entry(sym, ind)

    # ── Décision finale — seuil + filtre par paire ──
    if not assessment.trading_enabled:
        final_decision = "disabled"
    elif technical_bias == "neutral":
        final_decision = "skip_neutral"
    elif not execute_ok:
        final_decision = f"skip_{claude_reason}"
    elif scores.market_score < smc_engine.MARKET_MIN_SCORE:
        final_decision = "skip_low_score"
    elif not pair_ok:
        final_decision = f"skip_{pair_reason}"
    else:
        final_decision = "execute"

    # ── SMC Ultra gate (only when trade would otherwise execute) ──────
    smc_analysis = None
    if final_decision == "execute":
        tf_data = await asyncio.gather(
            market_data_service.get_klines(sym, "1m", 100),
            market_data_service.get_klines(sym, "5m", 100),
            market_data_service.get_klines(sym, "1h", 100),
            market_data_service.get_klines(sym, "4h", 100),
        )
        candles_by_tf = {
            "1m": tf_data[0], "5m": tf_data[1],
            "15m": candles,
            "1h": tf_data[2], "4h": tf_data[3],
        }
        smc_analysis = smc_engine.analyze(sym, candles_by_tf)
        log.info(
            "smc_analyzed",
            symbol=sym,
            smc_score=smc_analysis.smc_score,
            mtf=smc_analysis.mtf_score,
            bias=smc_analysis.bias,
            reason=smc_analysis.reason,
        )

        if smc_analysis.cooldown_blocked:
            final_decision = "skip_smc_cooldown"
        elif smc_analysis.asian_range_blocked:
            final_decision = "skip_asian_range"
        elif smc_analysis.smc_score < smc_engine.SMC_MIN_SCORE:
            final_decision = f"skip_smc_{smc_analysis.smc_score:.0f}"

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
        # SMC Ultra fields (present only when SMC analysis ran)
        "smc_score": smc_analysis.smc_score if smc_analysis else None,
        "smc_mtf": smc_analysis.mtf_score if smc_analysis else None,
        "smc_bias": smc_analysis.bias if smc_analysis else None,
        "smc_reason": smc_analysis.reason if smc_analysis else None,
        "smc_sl_price": smc_analysis.sl_price if smc_analysis else None,
        "smc_tp_price": smc_analysis.tp_price if smc_analysis else None,
        "smc_ob_high": smc_analysis.active_ob.high if (smc_analysis and smc_analysis.active_ob) else None,
        "smc_ob_low": smc_analysis.active_ob.low if (smc_analysis and smc_analysis.active_ob) else None,
    }


# ── Trade monitor — dedicated 30s loop, independent of analysis cycle ──────

MONITOR_INTERVAL = 30  # seconds


async def _close_trade_and_notify(db: AsyncSession, trade, price: float, reason: str):
    closed = await paper_trading_engine.close_paper_trade(db, trade.id, price)
    if reason == "sl":
        whatsapp_service.notify_stop_loss_hit(trade.symbol, price)
    else:
        whatsapp_service.notify_take_profit_hit(trade.symbol, price, closed.pnl_pct)
    whatsapp_service.notify_trade_closed(trade.symbol, closed.pnl_pct, closed.pnl_usd, closed.result)
    await db.commit()
    print(f"[MONITOR] {reason.upper()} CLOSED {trade.symbol} "
          f"entry={trade.entry_price} exit={price} pnl={closed.pnl_pct:+.2f}%")
    log.info(f"{reason}_triggered", symbol=trade.symbol, price=price, pnl_pct=closed.pnl_pct)


async def _monitor_open_trades():
    """
    Checks ALL open trades every 30s.
    Independent from the 5-min analysis cycle — catches TP/SL between cycles.
    """
    global _bot_running
    await asyncio.sleep(15)  # stagger vs analysis loop
    log.info("monitor_loop_started", interval_s=MONITOR_INTERVAL)

    while _bot_running:
        try:
            async with AsyncSessionLocal() as db:
                open_trades = await paper_trading_engine.get_open_trades(db)
                if not open_trades:
                    print("[MONITOR] No open trades.")
                else:
                    print(f"[MONITOR] Checking {len(open_trades)} open trade(s)...")

                for trade in open_trades:
                    try:
                        price = await market_data_service.get_price(trade.symbol)
                        if price is None:
                            print(f"[MONITOR] WARN: price=None for {trade.symbol} — skipping")
                            continue

                        tp_structure = trade.take_profit_structure or {}
                        first_tp = min(
                            (lvl["target_price"] for lvl in tp_structure.values()),
                            default=None,
                        )
                        print(
                            f"[MONITOR] {trade.symbol} | "
                            f"entry={trade.entry_price:.4f} | "
                            f"current={price:.4f} | "
                            f"sl={trade.stop_loss_price:.4f} | "
                            f"tp1={first_tp:.4f if first_tp else 'N/A'}"
                        )

                        if await paper_trading_engine.check_stop_loss(price, trade):
                            print(f"[MONITOR] → SL HIT {trade.symbol} ({price:.4f} <= {trade.stop_loss_price:.4f})")
                            await _close_trade_and_notify(db, trade, price, "sl")
                            continue

                        if await paper_trading_engine.check_take_profit(price, trade):
                            print(f"[MONITOR] → TP HIT {trade.symbol} ({price:.4f} >= {first_tp:.4f})")
                            await _close_trade_and_notify(db, trade, price, "tp")

                    except Exception as e:
                        print(f"[MONITOR] ERROR on {trade.symbol}: {e}")
                        log.error("monitor_trade_error", symbol=trade.symbol, error=str(e))

        except Exception as e:
            log.error("monitor_loop_error", error=str(e))
            print(f"[MONITOR] LOOP ERROR: {e}")

        await asyncio.sleep(MONITOR_INTERVAL)


# ── Stop-loss / TP helpers (secondary check inside analysis loop) ───────────

async def _check_stop_losses(db: AsyncSession, symbol: str):
    open_trades = await paper_trading_engine.get_open_trades(db)
    for trade in open_trades:
        if trade.symbol != symbol:
            continue
        price = await market_data_service.get_price(symbol)
        print(f"[CHECK SL] {trade.symbol} entry={trade.entry_price} current={price} sl={trade.stop_loss_price}")
        if price is None:
            print(f"[CHECK SL] WARN: price=None for {symbol}")
            continue
        if await paper_trading_engine.check_stop_loss(price, trade):
            print(f"[CHECK SL] → TRIGGERED {symbol} ({price} <= {trade.stop_loss_price})")
            closed = await paper_trading_engine.close_paper_trade(db, trade.id, price)
            whatsapp_service.notify_stop_loss_hit(symbol, price)
            whatsapp_service.notify_trade_closed(symbol, closed.pnl_pct, closed.pnl_usd, closed.result)
            log.info("stop_loss_triggered", symbol=symbol, price=price)


async def _check_take_profits(db: AsyncSession, symbol: str):
    open_trades = await paper_trading_engine.get_open_trades(db)
    for trade in open_trades:
        if trade.symbol != symbol:
            continue
        price = await market_data_service.get_price(symbol)
        tp_structure = trade.take_profit_structure or {}
        first_tp = min(
            (lvl["target_price"] for lvl in tp_structure.values()),
            default=None,
        )
        print(f"[CHECK TP] {trade.symbol} entry={trade.entry_price} current={price} tp1={first_tp}")
        if price is None:
            print(f"[CHECK TP] WARN: price=None for {symbol}")
            continue
        if await paper_trading_engine.check_take_profit(price, trade):
            print(f"[CHECK TP] → TRIGGERED {symbol} ({price} >= {first_tp})")
            closed = await paper_trading_engine.close_paper_trade(db, trade.id, price)
            whatsapp_service.notify_take_profit_hit(symbol, price, closed.pnl_pct)
            whatsapp_service.notify_trade_closed(symbol, closed.pnl_pct, closed.pnl_usd, closed.result)
            log.info("take_profit_triggered", symbol=symbol, price=price, pnl_pct=closed.pnl_pct)


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
                            confidence = analysis["scores"]["confidence_score"]
                            risk       = analysis["scores"]["risk_score"]
                            regime     = analysis["regime"]

                            # ── Discipline + DB guards ──
                            can_open, block_reason = await strategy_engine.can_open_trade(
                                db, symbol, analysis["scores"]["market_score"]
                            )
                            if not can_open:
                                log.info("trade_blocked", symbol=symbol, reason=block_reason)
                                continue

                            # ── Per-pair SL/TP + position sizing ──
                            pair_cfg = strategy_engine.get_pair_config(symbol)
                            size_pct = strategy_engine.apply_size_factor(
                                symbol, analysis["position_size_pct"]
                            )

                            # Prefer SMC-derived SL/TP (OB-anchored) over static config
                            smc_sl = analysis.get("smc_sl_price")
                            smc_tp = analysis.get("smc_tp_price")
                            if smc_sl and smc_tp and price > 0:
                                sl_pct = round((price - smc_sl) / price * 100, 4)
                                tp_pct = round((smc_tp - price) / price * 100, 4)
                                if sl_pct <= 0 or tp_pct <= 0:
                                    sl_pct = pair_cfg["sl_pct"]
                                    tp_pct = pair_cfg["tp_pct"]
                            else:
                                sl_pct = pair_cfg["sl_pct"]
                                tp_pct = pair_cfg["tp_pct"]

                            # OB details for WhatsApp
                            ob_info: str | None = None
                            if analysis.get("smc_ob_high") and analysis.get("smc_ob_low"):
                                ob_info = (
                                    f"OB ${analysis['smc_ob_low']:,.4f}–${analysis['smc_ob_high']:,.4f} "
                                    f"| SMC {analysis.get('smc_score', 0):.0f}/100 "
                                    f"| MTF {analysis.get('smc_mtf', 0)}/5"
                                )

                            if settings.paper_trading:
                                trade = await paper_trading_engine.open_paper_trade(
                                    db, symbol, price, size_pct, confidence, risk, regime,
                                    sl_pct=sl_pct, tp_pct=tp_pct,
                                )
                                strategy_engine.on_trade_opened(symbol)
                                smc_engine.record_signal(symbol)
                                whatsapp_service.notify_trade_opened(
                                    symbol, price, size_pct, trade.stop_loss_price, True,
                                    tp_pct=tp_pct, ob_info=ob_info,
                                )
                                log.info(
                                    "auto_paper_trade_opened",
                                    symbol=symbol, price=price, btc_regime=btc_regime,
                                    sl_pct=sl_pct, tp_pct=tp_pct,
                                    smc_score=analysis.get("smc_score"),
                                )
                            else:
                                order = await execution_service.execute_trade(
                                    symbol, price, size_pct, sl_pct
                                )
                                actual_price = order["actual_price"]
                                trade = await paper_trading_engine.open_paper_trade(
                                    db, symbol, actual_price, size_pct, confidence, risk, regime,
                                    sl_pct=sl_pct, tp_pct=tp_pct,
                                )
                                trade.is_paper = False
                                await db.flush()
                                strategy_engine.on_trade_opened(symbol)
                                smc_engine.record_signal(symbol)
                                whatsapp_service.notify_trade_opened(
                                    symbol, actual_price, size_pct, order["stop_loss_price"], False,
                                    tp_pct=tp_pct, ob_info=ob_info,
                                )

                        await _check_stop_losses(db, symbol)
                        await _check_take_profits(db, symbol)
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
    global _bot_task, _monitor_task, _bot_running
    await init_db()
    await market_data_service.start_ws_listener()
    _bot_running = True
    _bot_task = asyncio.create_task(auto_trading_loop())
    _monitor_task = asyncio.create_task(_monitor_open_trades())
    log.info("cryptomind_started", paper_trading=settings.paper_trading)
    yield
    _bot_running = False
    if _bot_task:
        _bot_task.cancel()
    if _monitor_task:
        _monitor_task.cancel()
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
    open_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.result == "open")
    )
    open_trades_count = int(open_result.scalar() or 0)
    return {
        "total_trades": snapshot.total_trades + open_trades_count,
        "open_trades": open_trades_count,
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


@app.delete("/api/trades/reset")
async def reset_trades(db: Annotated[AsyncSession, Depends(get_db)]):
    """Supprime tous les trades — repart à zéro pour un nouvel entraînement."""
    try:
        result = await db.execute(delete(Trade))
        deleted = result.rowcount
        await db.commit()
        log.info("trades_reset", deleted=deleted)
        return {"deleted": deleted, "message": f"{deleted} trades supprimés"}
    except Exception as e:
        log.error("trades_reset_error", error=str(e))
        raise HTTPException(500, f"Erreur reset: {str(e)}")


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