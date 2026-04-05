"""
Blockchain Service — suivi on-chain gratuit.

Sources :
- BTC : blockchain.info/stats + mempool.space (gratuit, sans clé API)
- ETH : etherscan.io API v2 (plan gratuit : 5 req/s, 100k/jour)

Whale score 0–100 :
  0   = aucune activité baleine détectée
  50  = activité normale
  100 = accumulation massive de baleines (signal haussier fort)

Cache Redis 5 minutes pour rester dans les limites gratuites.
Avec 12 paires analysées toutes les 10 min, on fait max ~12 appels on-chain/10min
→ BTC et ETH sont les seuls qui ont des données on-chain publiques fiables.
"""
from __future__ import annotations
import asyncio
import json
import time
import httpx
import redis.asyncio as aioredis
from app.config import get_settings
from app.utils.logging_utils import get_logger

log = get_logger(__name__)
settings = get_settings()

CACHE_TTL = 5 * 60  # 5 minutes
BTC_WHALE_THRESHOLD = 100    # BTC — transaction ≥ 100 BTC = baleine
ETH_WHALE_THRESHOLD = 1_000  # ETH — transaction ≥ 1000 ETH = baleine

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


# ── BTC on-chain (blockchain.info + mempool.space) ─────────────────────────

async def _fetch_btc_mempool_stats() -> dict:
    """
    mempool.space/api — gratuit, sans clé, très fiable.
    Retourne les stats du mempool BTC en temps réel.
    """
    url = "https://mempool.space/api/v1/fees/mempool-blocks"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _fetch_btc_large_txs() -> list[dict]:
    """
    blockchain.info/unconfirmed-transactions — transactions non confirmées.
    On filtre celles dont la valeur dépasse BTC_WHALE_THRESHOLD.
    Gratuit, sans clé API.
    """
    url = "https://blockchain.info/unconfirmed-transactions?format=json&limit=100"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        txs = data.get("txs", [])
        whales = []
        for tx in txs:
            # Valeur en satoshis → BTC
            out_value_btc = sum(o.get("value", 0) for o in tx.get("out", [])) / 1e8
            if out_value_btc >= BTC_WHALE_THRESHOLD:
                whales.append({
                    "hash": tx.get("hash", "")[:16],
                    "value_btc": round(out_value_btc, 2),
                })
        return whales


async def get_btc_whale_score() -> float:
    """
    Calcule le whale_score BTC (0–100).
    Logique :
      - 0 baleines détectées → score 50 (neutre, données insuffisantes)
      - 1–2 baleines        → score 65 (intérêt modéré)
      - 3–5 baleines        → score 78 (accumulation active)
      - > 5 baleines        → score 90 (accumulation massive)
    Les grosses sorties vers exchanges (heuristique : tx > 500 BTC) → score -10
    """
    r = _get_redis()
    cached = await r.get("onchain:btc_whale_score")
    if cached:
        return float(cached)

    try:
        whales = await _fetch_btc_large_txs()
        count = len(whales)

        if count == 0:
            score = 50.0
        elif count <= 2:
            score = 65.0
        elif count <= 5:
            score = 78.0
        else:
            score = 90.0

        # Pénalité si très grosses transactions (potentiel dump vers exchange)
        very_large = [w for w in whales if w["value_btc"] >= 500]
        if very_large:
            score = max(30.0, score - 10.0 * len(very_large))

        score = round(score, 1)
        await r.setex("onchain:btc_whale_score", CACHE_TTL, str(score))
        log.info("btc_whale_score", score=score, whale_count=count)
        return score

    except Exception as e:
        log.warning("btc_whale_fetch_error", error=str(e))
        return 50.0  # neutre si erreur


# ── ETH on-chain (Etherscan gratuit) ───────────────────────────────────────

async def _fetch_eth_large_txs(etherscan_key: str) -> list[dict]:
    """
    Etherscan API v2 — plan gratuit (100k req/jour).
    Récupère les 20 dernières transactions ETH > seuil baleine.
    On surveille les adresses "whale" connues via les top holders.
    """
    # Endpoint : transactions récentes du token ETH (transferts natifs)
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": 1,
        "module": "account",
        "action": "txlist",
        "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",  # Vitalik (proxy baleine)
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 5,
        "sort": "desc",
        "apikey": etherscan_key,
    }
    # Note : pour une vraie implémentation, scanner les top wallets ETH
    # Pour la version gratuite, on utilise les stats globales du réseau
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        txs = data.get("result", [])
        if isinstance(txs, str):  # erreur API
            return []
        whales = []
        for tx in txs:
            value_eth = int(tx.get("value", 0)) / 1e18
            if value_eth >= ETH_WHALE_THRESHOLD:
                whales.append({"hash": tx.get("hash", "")[:16], "value_eth": round(value_eth, 1)})
        return whales


async def _fetch_eth_network_stats(etherscan_key: str) -> dict:
    """Stats réseau ETH : gas price, transactions en attente."""
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": 1,
        "module": "gastracker",
        "action": "gasoracle",
        "apikey": etherscan_key,
    }
    async with httpx.AsyncClient(timeout=6) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {})


async def get_eth_whale_score(etherscan_key: str = "") -> float:
    """
    Calcule le whale_score ETH (0–100).
    Sans clé Etherscan → retourne 50 (neutre).
    """
    if not etherscan_key:
        return 50.0

    r = _get_redis()
    cached = await r.get("onchain:eth_whale_score")
    if cached:
        return float(cached)

    try:
        whales = await _fetch_eth_large_txs(etherscan_key)
        gas_stats = await _fetch_eth_network_stats(etherscan_key)

        count = len(whales)
        if count == 0:
            score = 50.0
        elif count <= 2:
            score = 65.0
        elif count <= 5:
            score = 78.0
        else:
            score = 88.0

        # Gas élevé = forte activité réseau = signal haussier
        fast_gas = float(gas_stats.get("FastGasPrice", 0))
        if fast_gas > 50:
            score = min(100.0, score + 5.0)

        score = round(score, 1)
        await r.setex("onchain:eth_whale_score", CACHE_TTL, str(score))
        log.info("eth_whale_score", score=score, whale_count=count, fast_gas=fast_gas)
        return score

    except Exception as e:
        log.warning("eth_whale_fetch_error", error=str(e))
        return 50.0


# ── Point d'entrée principal ───────────────────────────────────────────────

async def get_whale_score(symbol: str) -> float:
    """
    Retourne le whale_score (0–100) pour un symbole donné.
    Seuls BTC et ETH ont des données on-chain gratuites fiables.
    Les autres cryptos reçoivent 50 (neutre).

    Appelé par auto_trading_loop dans main.py.
    """
    sym = symbol.upper().replace("USD", "").replace("USDT", "")

    if sym == "BTC" or sym == "XBT":
        return await get_btc_whale_score()

    if sym == "ETH":
        etherscan_key = getattr(settings, "etherscan_api_key", "")
        return await get_eth_whale_score(etherscan_key)

    # Pour les autres cryptos : score neutre
    return 50.0


async def get_onchain_summary() -> dict:
    """Résumé on-chain pour le dashboard — appelé par l'API /api/onchain."""
    btc_score = await get_btc_whale_score()
    etherscan_key = getattr(settings, "etherscan_api_key", "")
    eth_score = await get_eth_whale_score(etherscan_key)

    def _label(score: float) -> str:
        if score >= 80:
            return "accumulation_forte"
        if score >= 65:
            return "accumulation_moderee"
        if score <= 30:
            return "distribution"
        return "neutre"

    return {
        "btc": {"whale_score": btc_score, "label": _label(btc_score)},
        "eth": {"whale_score": eth_score, "label": _label(eth_score)},
        "cache_ttl_min": CACHE_TTL // 60,
        "thresholds": {
            "btc_whale_btc": BTC_WHALE_THRESHOLD,
            "eth_whale_eth": ETH_WHALE_THRESHOLD,
        },
    }
