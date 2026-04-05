"""
Market Data Service — Kraken WebSocket (v1) + Kraken REST OHLC.
Prix stockés en mémoire (dict) — pas besoin de Redis.
"""
from __future__ import annotations
import asyncio
import json
import time
import httpx
import websockets
from app.config import get_settings
from app.utils.logging_utils import get_logger

log = get_logger(__name__)
settings = get_settings()

# ── Paires Kraken (format WS v1) ────────────────────────────────────────────
KRAKEN_PAIRS = [
    "XBT/USD", "ETH/USD", "SOL/USD", "ADA/USD", "DOT/USD",
    "XRP/USD", "LINK/USD", "LTC/USD", "BCH/USD", "XLM/USD",
    "AVAX/USD", "ATOM/USD", "ALGO/USD", "NEAR/USD", "TRX/USD",
    "UNI/USD",
]

_PAIR_TO_KEY: dict[str, str] = {
    "XBT/USD":  "BTCUSD",
    "ETH/USD":  "ETHUSD",
    "SOL/USD":  "SOLUSD",
    "ADA/USD":  "ADAUSD",
    "DOT/USD":  "DOTUSD",
    "XRP/USD":  "XRPUSD",
    "LINK/USD": "LINKUSD",
    "LTC/USD":  "LTCUSD",
    "BCH/USD":  "BCHUSD",
    "XLM/USD":  "XLMUSD",
    "AVAX/USD": "AVAXUSD",
    "ATOM/USD": "ATOMUSD",
    "ALGO/USD": "ALGOUSD",
    "NEAR/USD": "NEARUSD",
    "TRX/USD":  "TRXUSD",
    "UNI/USD":  "UNIUSD",
}

_REDIS_TO_OHLC_PAIR: dict[str, str] = {
    "BTCUSD":  "XBTUSD",
    "ETHUSD":  "ETHUSD",
    "SOLUSD":  "SOLUSD",
    "ADAUSD":  "ADAUSD",
    "DOTUSD":  "DOTUSD",
    "XRPUSD":  "XRPUSD",
    "LINKUSD": "LINKUSD",
    "LTCUSD":  "LTCUSD",
    "BCHUSD":  "BCHUSD",
    "XLMUSD":  "XLMUSD",
    "AVAXUSD": "AVAXUSD",
    "ATOMUSD": "ATOMUSD",
    "ALGOUSD": "ALGOUSD",
    "NEARUSD": "NEARUSD",
    "TRXUSD":  "TRXUSD",
    "UNIUSD":  "UNIUSD",
}

_INTERVAL_MAP: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}

KRAKEN_WS_URL = "wss://ws.kraken.com"
KRAKEN_REST_URL = "https://api.kraken.com/0/public"

# ── Stockage en mémoire (remplace Redis) ─────────────────────────────────────
_price_cache: dict[str, float] = {}          # {"BTCUSD": 42000.0}
_ticker_cache: dict[str, dict] = {}          # {"BTCUSD": {price, ask, bid, ...}}
_cache_ts: dict[str, float] = {}             # {"BTCUSD": timestamp}
_CACHE_TTL = 60.0                            # secondes (même TTL qu'avant avec Redis)

_ws_connected: bool = False


# ── Helpers cache mémoire ────────────────────────────────────────────────────

def _set_price(key: str, price: float) -> None:
    _price_cache[key] = price
    _cache_ts[key] = time.time()


def _get_price_cached(key: str) -> float | None:
    ts = _cache_ts.get(key)
    if ts and (time.time() - ts) < _CACHE_TTL:
        return _price_cache.get(key)
    return None


def _set_ticker(key: str, data: dict) -> None:
    _ticker_cache[key] = data


def _get_ticker_cached(key: str) -> dict | None:
    ts = _cache_ts.get(key)
    if ts and (time.time() - ts) < _CACHE_TTL:
        return _ticker_cache.get(key)
    return None


# ── API publique ─────────────────────────────────────────────────────────────

async def get_price(symbol: str) -> float | None:
    """Retourne le dernier prix depuis le cache mémoire."""
    key = _normalize_key(symbol)
    return _get_price_cached(key)


async def get_full_ticker(symbol: str) -> dict | None:
    """Retourne le ticker complet depuis le cache mémoire."""
    key = _normalize_key(symbol)
    return _get_ticker_cached(key)


async def get_all_prices() -> dict[str, float]:
    """Retourne tous les prix en cache sous forme {symbol: price}."""
    now = time.time()
    return {
        k: v for k, v in _price_cache.items()
        if (now - _cache_ts.get(k, 0)) < _CACHE_TTL
    }


def is_connected() -> bool:
    return _ws_connected


# ── OHLC — Kraken REST ───────────────────────────────────────────────────────

async def get_klines(symbol: str, interval: str = "1h", limit: int = 210) -> list[dict]:
    """Récupère les bougies OHLCV depuis l'API REST Kraken."""
    key = _normalize_key(symbol)
    ohlc_pair = _REDIS_TO_OHLC_PAIR.get(key)
    if not ohlc_pair:
        log.warning("unknown_pair_for_ohlc", symbol=symbol, key=key)
        return []

    interval_min = _INTERVAL_MAP.get(interval, 60)
    url = f"{KRAKEN_REST_URL}/OHLC"
    params = {"pair": ohlc_pair, "interval": interval_min}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("error"):
                log.error("kraken_ohlc_api_error", errors=data["error"], pair=ohlc_pair)
                return []

            result = data.get("result", {})
            candles_raw = next(
                (v for k, v in result.items() if k != "last"),
                []
            )

            candles = [
                {
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[6]),
                }
                for c in candles_raw
            ]
            candles = candles[-limit:]
            log.debug("kraken_ohlc_ok", pair=ohlc_pair, count=len(candles))
            return candles

    except Exception as e:
        log.error("kraken_ohlc_error", pair=ohlc_pair, error=str(e))
        return []


# ── Fear & Greed ──────────────────────────────────────────────────────────────

async def get_fear_greed_index() -> float | None:
    """Fear & Greed Index depuis Alternative.me (gratuit, sans auth)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=1")
            resp.raise_for_status()
            data = resp.json()
            value = float(data["data"][0]["value"])
            log.debug("fear_greed_ok", value=value)
            return value
    except Exception as e:
        log.warning("fear_greed_error", error=str(e))
        return None


# ── Kraken WebSocket listener ─────────────────────────────────────────────────

async def _ws_listener():
    """
    Connecte au WebSocket Kraken v1.
    Les prix sont stockés en mémoire au lieu de Redis.
    """
    global _ws_connected

    subscribe_msg = json.dumps({
        "event": "subscribe",
        "pair": KRAKEN_PAIRS,
        "subscription": {"name": "ticker"},
    })

    while True:
        try:
            async with websockets.connect(
                KRAKEN_WS_URL,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                await ws.send(subscribe_msg)
                _ws_connected = True
                log.info("kraken_ws_connected", pairs=len(KRAKEN_PAIRS))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)

                        if isinstance(msg, dict):
                            event = msg.get("event", "")
                            if event == "heartbeat":
                                continue
                            if event == "subscriptionStatus":
                                status = msg.get("status")
                                pair = msg.get("pair", "")
                                if status == "subscribed":
                                    log.debug("kraken_ws_subscribed", pair=pair)
                                elif status == "error":
                                    log.warning("kraken_ws_sub_error", pair=pair, msg=msg.get("errorMessage"))
                            continue

                        if not isinstance(msg, list) or len(msg) < 4:
                            continue
                        if msg[2] != "ticker":
                            continue

                        kraken_pair = msg[3]
                        d = msg[1]

                        price    = float(d["c"][0])
                        ask      = float(d["a"][0])
                        bid      = float(d["b"][0])
                        vol24h   = float(d["v"][1])
                        open24h  = float(d["o"][1])
                        change24h = round((price - open24h) / open24h * 100, 2) if open24h else 0.0

                        key = _PAIR_TO_KEY.get(kraken_pair)
                        if not key:
                            continue

                        # Stockage en mémoire
                        _set_price(key, price)
                        _set_ticker(key, {
                            "pair": kraken_pair,
                            "price": price,
                            "ask": ask,
                            "bid": bid,
                            "vol24h": round(vol24h / 1e6, 3),
                            "change24h": change24h,
                            "ts": time.time(),
                        })

                    except (KeyError, ValueError, IndexError) as e:
                        log.debug("kraken_ws_parse_skip", error=str(e))

        except websockets.exceptions.ConnectionClosed as e:
            _ws_connected = False
            log.warning("kraken_ws_closed", code=e.code, reason=e.reason)
        except Exception as e:
            _ws_connected = False
            log.error("kraken_ws_error", error=str(e))
        finally:
            _ws_connected = False

        log.info("kraken_ws_reconnecting", delay_s=5)
        await asyncio.sleep(5)


async def start_ws_listener():
    """Lance le listener WebSocket Kraken en tâche de fond."""
    asyncio.create_task(_ws_listener())
    log.info("kraken_ws_listener_scheduled")


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize_key(symbol: str) -> str:
    """
    Normalise vers la clé interne.
    "BTC/USD" → "BTCUSD"
    "BTCUSDT" → "BTCUSD"
    "XBT/USD" → "BTCUSD"
    """
    s = symbol.upper().replace("/", "").replace("-", "")
    s = s.replace("XBT", "BTC")
    if s.endswith("USDT"):
        s = s[:-1]
    return s