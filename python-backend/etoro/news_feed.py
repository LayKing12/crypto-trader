"""News / sentiment : score lexical -1..1 à partir de titres d'actualité.

Ordre des sources (voir `docs/news_sources.md`) :
1. eToro — feed social d'un instrument (`GET /api/v1/feeds/markets/{marketId}`), seulement si
   `etoro_api_key` ET `etoro_user_key` sont configurés. eToro n'expose ni Tori, ni Grok, ni un
   score de sentiment via l'API publique : on scorera nous-mêmes le texte des posts.
2. NewsAPI (`https://newsapi.org/v2/everything`) si `settings.news_api_key`.
3. Flux RSS Yahoo Finance (`https://feeds.finance.yahoo.com/rss/2.0/headline?s=...`).

Aucune exception ne remonte à l'appelant : toute erreur est loguée et donne `None`.
"""
from __future__ import annotations

import calendar
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from .config import Settings

log = logging.getLogger(__name__)

# --- Constantes -----------------------------------------------------------------

NEWSAPI_URL = "https://newsapi.org/v2/everything"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
ETORO_FEED_PATH = "/api/v1/feeds/markets/{market_id}"

MAX_HEADLINES = 20
CACHE_TTL_S = 15 * 60
FRESH_WINDOW = timedelta(hours=24)
FRESH_WEIGHT = 2.0
STALE_WEIGHT = 1.0
NEWSAPI_LOOKBACK = timedelta(days=7)

# Lexique financier anglais (minuscules, mots entiers).
POSITIVE_WORDS: frozenset[str] = frozenset(
    {
        "beat", "beats", "surge", "surges", "surged", "soar", "soars", "soared", "upgrade", "upgrades",
        "upgraded", "record", "rally", "rallies", "rallied", "jump", "jumps", "jumped", "gain", "gains",
        "gained", "rise", "rises", "rose", "climb", "climbs", "climbed", "bullish", "outperform",
        "outperforms", "strong", "growth", "profit", "profits", "profitable", "buy", "boost", "boosts",
        "boosted", "raise", "raises", "raised", "high", "highs", "rebound", "rebounds", "recover",
        "recovers", "recovery", "optimism", "optimistic", "breakout", "momentum", "win", "wins",
        "dividend", "buyback", "approval", "approved", "expand", "expands", "expansion", "upside",
        "top", "tops", "exceed", "exceeds", "exceeded", "positive", "robust", "accelerate", "accelerates",
    }
)
NEGATIVE_WORDS: frozenset[str] = frozenset(
    {
        "miss", "misses", "missed", "plunge", "plunges", "plunged", "downgrade", "downgrades", "downgraded",
        "lawsuit", "lawsuits", "sue", "sues", "sued", "recall", "recalls", "recalled", "cut", "cuts",
        "slash", "slashes", "slashed", "fall", "falls", "fell", "drop", "drops", "dropped", "tumble",
        "tumbles", "tumbled", "slump", "slumps", "slumped", "crash", "crashes", "crashed", "bearish",
        "underperform", "underperforms", "weak", "weakness", "loss", "losses", "sell", "selloff",
        "sell-off", "decline", "declines", "declined", "warning", "warns", "warned", "probe",
        "investigation", "fine", "fined", "penalty", "fraud", "layoff", "layoffs", "bankruptcy",
        "bankrupt", "default", "risk", "risks", "fear", "fears", "concern", "concerns", "worry",
        "worries", "low", "lows", "downside", "volatile", "volatility", "recession", "inflation",
        "tariff", "tariffs", "ban", "banned", "halt", "halts", "halted", "delay", "delays", "delayed",
        "disappoint", "disappoints", "disappointing", "negative", "pressure", "sink", "sinks", "sank",
    }
)

# symbole -> requête texte (NewsAPI) ; défaut : le symbole lui-même.
SYMBOL_QUERIES: dict[str, str] = {
    "XAUUSD": "gold price OR XAU/USD",
    "SPY": "S&P 500",
    "AAPL": "Apple OR AAPL",
    "MSFT": "Microsoft OR MSFT",
    "NVDA": "Nvidia OR NVDA",
    "AMZN": "Amazon OR AMZN",
    "GOOGL": "Alphabet OR Google OR GOOGL",
}
# symbole -> ticker Yahoo Finance ; défaut : le symbole lui-même.
YAHOO_SYMBOLS: dict[str, str] = {
    "XAUUSD": "GC=F",
}

_WORD_RE = re.compile(r"[a-z][a-z\-']*")
_cache: dict[str, tuple[float, float]] = {}  # symbol -> (expires_at_monotonic, score)


# --- Scoring pur ----------------------------------------------------------------


def _score_one(title: str) -> float:
    words = _WORD_RE.findall(title.lower())
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    return (pos - neg) / max(1, pos + neg)


def score_headlines(titles: list[str]) -> float:
    """Score lexical moyen (-1..1) d'une liste de titres, sans pondération. Liste vide => 0.0."""
    titles = [t for t in titles if t and t.strip()][:MAX_HEADLINES]
    if not titles:
        return 0.0
    return _clamp(sum(_score_one(t) for t in titles) / len(titles))


def score_items(items: list[tuple[str, datetime | None]], now: datetime | None = None) -> float:
    """Comme `score_headlines`, mais un titre publié il y a moins de 24 h pèse double."""
    now = now or datetime.now(timezone.utc)
    items = [(t, d) for t, d in items if t and t.strip()][:MAX_HEADLINES]
    if not items:
        return 0.0
    total = 0.0
    weights = 0.0
    for title, published in items:
        w = FRESH_WEIGHT if published is not None and (now - published) < FRESH_WINDOW else STALE_WEIGHT
        total += w * _score_one(title)
        weights += w
    return _clamp(total / weights)


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


# --- Helpers ----------------------------------------------------------------------


def query_for(symbol: str) -> str:
    return SYMBOL_QUERIES.get(symbol.upper(), symbol.upper())


def yahoo_symbol_for(symbol: str) -> str:
    return YAHOO_SYMBOLS.get(symbol.upper(), symbol.upper())


def clear_cache() -> None:
    _cache.clear()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- Sources ----------------------------------------------------------------------


async def _fetch_etoro(symbol: str, settings: Settings, client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Posts du feed social eToro de l'instrument. [] si non configuré (x-user-key requis)."""
    if not settings.etoro_api_key or not settings.etoro_user_key:
        return []
    url = settings.base_url.rstrip("/") + ETORO_FEED_PATH.format(market_id=symbol.upper())
    headers = {
        "x-api-key": settings.etoro_api_key,
        "x-user-key": settings.etoro_user_key,
        "x-request-id": str(uuid.uuid4()),
    }
    resp = await client.get(url, headers=headers, params={"take": MAX_HEADLINES}, timeout=settings.etoro_timeout_s)
    resp.raise_for_status()
    items: list[tuple[str, datetime | None]] = []
    for disc in resp.json().get("discussions") or []:
        post = (disc or {}).get("post") or {}
        text = ((post.get("message") or {}).get("text") or "").strip()
        if text:
            items.append((text, _parse_iso(post.get("created"))))
    return items


async def _fetch_newsapi(symbol: str, settings: Settings, client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    if not settings.news_api_key:
        return []
    since = (datetime.now(timezone.utc) - NEWSAPI_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "q": query_for(symbol),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": MAX_HEADLINES,
        "from": since,
    }
    resp = await client.get(
        NEWSAPI_URL, params=params, headers={"X-Api-Key": settings.news_api_key}, timeout=settings.etoro_timeout_s
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI status={data.get('status')} code={data.get('code')}")
    items: list[tuple[str, datetime | None]] = []
    for art in data.get("articles") or []:
        title = (art.get("title") or "").strip()
        if title:
            items.append((title, _parse_iso(art.get("publishedAt"))))
    return items


async def _fetch_yahoo_rss(symbol: str, settings: Settings, client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    resp = await client.get(YAHOO_RSS_URL, params={"s": yahoo_symbol_for(symbol)}, timeout=settings.etoro_timeout_s)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    items: list[tuple[str, datetime | None]] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        published = None
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if struct:
            published = datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)
        items.append((title, published))
    return items


_SOURCES = (("etoro", _fetch_etoro), ("newsapi", _fetch_newsapi), ("yahoo_rss", _fetch_yahoo_rss))


# --- API publique ------------------------------------------------------------------


async def get_sentiment(symbol: str, settings: Settings, client: httpx.AsyncClient) -> float | None:
    """Sentiment -1..1 pour `symbol` (cache 15 min). None si aucune source n'a répondu."""
    try:
        key = symbol.upper()
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        for name, fetch in _SOURCES:
            try:
                items = await fetch(key, settings, client)
            except Exception as exc:  # noqa: BLE001 - jamais d'exception vers l'appelant
                log.warning("news_feed: source %s failed for %s: %s", name, key, exc)
                continue
            if not items:
                log.debug("news_feed: source %s returned nothing for %s", name, key)
                continue
            score = score_items(items)
            _cache[key] = (time.monotonic() + CACHE_TTL_S, score)
            log.info("news_feed: %s sentiment=%.3f via %s (%d titles)", key, score, name, len(items))
            return score
        log.warning("news_feed: no sentiment available for %s", key)
        return None
    except Exception as exc:  # noqa: BLE001
        log.error("news_feed: unexpected error for %s: %s", symbol, exc)
        return None
