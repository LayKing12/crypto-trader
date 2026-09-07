"""Small caps crypto en observation seule : top 50-100 par capitalisation, négociables sur Kraken.

Source : CoinGecko `/coins/markets` (gratuit, sans clé) pour le classement, API publique Kraken
`/0/public/AssetPairs` pour ne garder que les paires USD réellement cotées sur Kraken.
Ces paires alimentent uniquement `MarketWatch` (journal des mouvements) : elles n'entrent jamais
dans l'univers de trading ni dans `signal_service`. Cache 24 h, jamais d'exception vers l'appelant.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
CACHE_TTL_S = 24 * 3600
# Alias CoinGecko -> Kraken pour les symboles qui diffèrent
KRAKEN_ALIASES = {"BTC": "XBT", "DOGE": "XDG"}
STABLECOINS = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "USDS", "PYUSD", "USD1", "USDD", "EURC"}
WRAPPED_OR_STAKED = {"WBTC", "WETH", "STETH", "WSTETH", "WEETH", "RETH", "CBBTC", "CBETH", "BSC-USD", "LEO", "WBNB"}

_cache: dict[str, Any] = {"at": 0.0, "pairs": [], "meta": []}


def reset_cache() -> None:
    _cache.update({"at": 0.0, "pairs": [], "meta": []})


def _kraken_usd_pairs(payload: dict[str, Any]) -> dict[str, str]:
    """{symbole base (alias Kraken) -> nom de paire Kraken 'wsname' ou altname} pour les paires /USD."""
    out: dict[str, str] = {}
    for _, info in (payload.get("result") or {}).items():
        if not isinstance(info, dict):
            continue
        wsname = str(info.get("wsname") or "")
        altname = str(info.get("altname") or "")
        if not wsname.endswith("/USD") or ".d" in altname:
            continue
        base = wsname.split("/")[0].upper()
        out.setdefault(base, altname or wsname.replace("/", ""))
    return out


def select_smallcaps(markets: list[dict[str, Any]], kraken_pairs: dict[str, str],
                     rank_from: int = 51, rank_to: int = 100) -> list[dict[str, Any]]:
    """Retient les rangs [rank_from, rank_to] présents sur Kraken, hors stablecoins et jetons wrappés."""
    out = []
    for m in markets:
        rank = m.get("market_cap_rank")
        symbol = str(m.get("symbol") or "").upper()
        if not isinstance(rank, int) or rank < rank_from or rank > rank_to:
            continue
        if symbol in STABLECOINS or symbol in WRAPPED_OR_STAKED:
            continue
        kraken_symbol = KRAKEN_ALIASES.get(symbol, symbol)
        pair = kraken_pairs.get(kraken_symbol)
        if not pair:
            continue
        out.append({"symbol": symbol, "pair": pair, "rank": rank, "name": m.get("name"),
                    "market_cap_usd": m.get("market_cap")})
    return out


async def smallcap_pairs(client: httpx.AsyncClient, rank_from: int = 51, rank_to: int = 100,
                         now: float | None = None) -> list[dict[str, Any]]:
    """Liste [{symbol, pair, rank, name, market_cap_usd}] triée par rang. Cache 24 h ; [] si indisponible."""
    now = now or time.time()
    if _cache["meta"] and now - _cache["at"] < CACHE_TTL_S:
        return list(_cache["meta"])
    try:
        cg = await client.get(COINGECKO_MARKETS_URL, params={"vs_currency": "usd", "order": "market_cap_desc",
                                                           "per_page": 100, "page": 1, "sparkline": "false"},
                              timeout=15.0)
        cg.raise_for_status()
        kr = await client.get(KRAKEN_ASSET_PAIRS_URL, timeout=15.0)
        kr.raise_for_status()
        meta = sorted(select_smallcaps(cg.json(), _kraken_usd_pairs(kr.json()), rank_from, rank_to),
                      key=lambda m: m["rank"])
        _cache.update({"at": now, "meta": meta, "pairs": [m["pair"] for m in meta]})
        log.info("watch small caps : %d paires Kraken entre les rangs %d et %d", len(meta), rank_from, rank_to)
        return list(meta)
    except Exception as exc:  # noqa: BLE001
        log.warning("watch small caps : liste indisponible (%s)", exc)
        return list(_cache["meta"])
