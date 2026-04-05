"""
Execution Service — live order execution on Kraken via CCXT.
Only called when PAPER_TRADING=false.
Double-checks balance before every order.
"""
from __future__ import annotations
import ccxt.async_support as ccxt
from app.config import get_settings
from app.utils.logging_utils import get_logger
import app.services.whatsapp_service as wa

log = get_logger(__name__)
settings = get_settings()

_exchange: ccxt.kraken | None = None


def _get_exchange() -> ccxt.kraken:
    global _exchange
    if _exchange is None:
        _exchange = ccxt.kraken({
            "apiKey": settings.kraken_api_key,
            "secret": settings.kraken_secret_key,
            "enableRateLimit": True,
        })
    return _exchange


async def get_usd_balance() -> float:
    try:
        exchange = _get_exchange()
        balance = await exchange.fetch_balance()
        return float(balance.get("ZUSD", {}).get("free", 0.0))
    except Exception as e:
        log.error("kraken_balance_error", error=str(e))
        return 0.0


async def execute_trade(
    symbol: str,
    entry_price: float,
    position_size_pct: float,
    stop_loss_pct: float,
) -> dict:
    if settings.paper_trading:
        raise RuntimeError("execute_trade called in paper trading mode — use paper_trading_engine instead")

    exchange = _get_exchange()

    balance = await get_usd_balance()
    amount_usd = balance * position_size_pct / 100
    if amount_usd < 10:
        raise ValueError(f"Insufficient balance: ${balance:.2f} → ${amount_usd:.2f} for {position_size_pct}%")

    amount_base = round(amount_usd / entry_price, 6)
    kraken_symbol = symbol.replace("USDT", "/USDT")

    log.info("executing_buy", symbol=kraken_symbol, amount_usd=amount_usd, amount_base=amount_base)

    try:
        buy_order = await exchange.create_market_buy_order(kraken_symbol, amount_base)
        actual_price = float(buy_order.get("average", entry_price))

        stop_price = round(actual_price * (1 - stop_loss_pct / 100), 4)
        limit_price = round(stop_price * 0.995, 4)

        sl_order = await exchange.create_order(
            kraken_symbol,
            "stop_loss_limit",
            "sell",
            amount_base,
            limit_price,
            {"stopPrice": stop_price},
        )

        log.info(
            "trade_executed",
            symbol=kraken_symbol,
            buy_id=buy_order.get("id"),
            sl_id=sl_order.get("id"),
            stop_price=stop_price,
        )

        # ── Notification WhatsApp ──
        wa.notify_trade_opened(
            symbol=symbol,
            price=actual_price,
            size_pct=position_size_pct,
            stop_loss=stop_price,
            is_paper=False,
        )

        return {
            "buy_order": buy_order,
            "stop_loss_order": sl_order,
            "actual_price": actual_price,
            "stop_loss_price": stop_price,
        }

    except Exception as e:
        log.error("kraken_order_error", symbol=kraken_symbol, error=str(e))
        wa.notify_risk_alert(f"Ordre échoué sur {symbol} : {str(e)}")
        raise


async def close_exchange():
    global _exchange
    if _exchange:
        await _exchange.close()
        _exchange = None