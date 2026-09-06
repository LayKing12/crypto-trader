"""Veille de l'actualité : RSS Yahoo Finance par ticker + CryptoPanic (si token).

Sources (réutilise les constantes de `etoro/news_feed.py`, sans scoring) :
- Yahoo Finance RSS : `https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL` (actions
  eToro suivies) et `?s=BTC-USD` (bases des paires Kraken).
- CryptoPanic API v1 : `https://cryptopanic.com/api/v1/posts/?auth_token=...&currencies=BTC,ETH`
  ignorée sans `CRYPTOPANIC_TOKEN`.

Chaque titre est filtré sur les actifs suivis (ticker ou nom dans le titre), dédoublonné par
URL ou titre (mémoire des 2000 derniers) puis journalisé comme Observation `news`.
Aucune action déclenchée, aucun score de sentiment : observation uniquement.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import re
from collections import deque
from datetime import UTC, datetime, timezone
from typing import Any

import feedparser
import httpx

from etoro.news_feed import YAHOO_RSS_URL, yahoo_symbol_for

from .config import WatchSettings
from .observation_log import Observation, ObservationLog, new_observation

log = logging.getLogger(__name__)

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
DEDUPE_SIZE = 2000
HTTP_TIMEOUT_S = 15.0

# Noms usuels associés aux tickers (insensibles à la casse). Le ticker lui-même est toujours
# accepté (sensible à la casse, mot entier) pour éviter que "META" matche "meta-analysis".
SYMBOL_NAMES: dict[str, tuple[str, ...]] = {
    "AAPL": ("Apple",),
    "MSFT": ("Microsoft",),
    "NVDA": ("Nvidia",),
    "AMZN": ("Amazon",),
    "GOOGL": ("Alphabet", "Google"),
    "GOOG": ("Alphabet", "Google"),
    "META": ("Meta Platforms", "Facebook", "Instagram"),
    "NEM": ("Newmont",),
    "AEM": ("Agnico Eagle", "Agnico"),
    "SPY": ("S&P 500",),
    "XAUUSD": ("gold",),
    "BTC": ("Bitcoin",),
    "ETH": ("Ethereum", "Ether"),
    "SOL": ("Solana",),
    "XRP": ("Ripple",),
    "ADA": ("Cardano",),
    "DOT": ("Polkadot",),
    "LINK": ("Chainlink",),
    "AVAX": ("Avalanche",),
    "ATOM": ("Cosmos",),
    "NEAR": ("NEAR Protocol",),
    "ALGO": ("Algorand",),
    "LTC": ("Litecoin",),
}


def _ticker_regex(ticker: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(ticker) + r"(?![A-Za-z0-9])")


def _name_regex(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.IGNORECASE)


class NewsWatch:
    """Poll périodique des sources d'actualité, filtrage par actifs suivis, dédoublonnage."""

    def __init__(self, settings: WatchSettings, obs_log: ObservationLog, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._log = obs_log
        self._client = client
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._source_failing: dict[str, bool] = {}
        self._patterns: dict[str, list[re.Pattern[str]]] = {}
        for sym in self.watched_symbols:
            pats = [_ticker_regex(sym)]
            pats.extend(_name_regex(n) for n in SYMBOL_NAMES.get(sym, ()))
            self._patterns[sym] = pats
        self.last_tick_at: datetime | None = None
        self.tick_count = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ état
    @property
    def watched_symbols(self) -> list[str]:
        out: list[str] = []
        for s in self._settings.etoro_symbols + self._settings.crypto_bases:
            if s not in out:
                out.append(s)
        return out

    @property
    def sources(self) -> list[str]:
        active = [s for s in self._settings.news_sources if s == "yahoo"]
        if self._settings.cryptopanic_enabled:
            active.append("cryptopanic")
        return active

    def status(self) -> dict[str, Any]:
        return {
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "tick_count": self.tick_count,
            "last_error": self.last_error,
            "sources": self.sources,
            "symbols": self.watched_symbols,
            "seen": len(self._seen),
        }

    # ------------------------------------------------------------------ helpers
    def match_symbol(self, title: str, preferred: str | None = None) -> str | None:
        """Premier actif suivi cité dans le titre (ticker ou nom), `preferred` en priorité."""
        if not title:
            return None
        order = list(self.watched_symbols)
        if preferred and preferred in self._patterns:
            order.remove(preferred)
            order.insert(0, preferred)
        for sym in order:
            for pat in self._patterns.get(sym, ()):
                if pat.search(title):
                    return sym
        return None

    def _is_new(self, key: str) -> bool:
        """Dédoublonnage : mémoire bornée des `DEDUPE_SIZE` dernières clés (URL ou titre)."""
        if key in self._seen:
            return False
        self._seen.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > DEDUPE_SIZE:
            self._seen.discard(self._seen_order.popleft())
        return True

    def _record_news(self, source: str, title: str, url: str | None, published: datetime | None,
                     symbol: str | None, extra: dict[str, Any] | None = None) -> Observation | None:
        key = (url or "").strip() or title.strip()
        if not key or not self._is_new(key):
            return None
        detail: dict[str, Any] = {
            "title": title,
            "source": source,
            "url": url,
            "published": published.isoformat() if published else None,
            "symbol": symbol,
        }
        if extra:
            detail.update(extra)
        return self._log.record(new_observation(
            "news", source, title=title, symbol=symbol, detail=detail,  # type: ignore[arg-type]
        ))

    def _note_source_error(self, source: str, exc: BaseException) -> None:
        self.last_error = f"{source}: {type(exc).__name__}: {exc}"
        log.warning("watch news : source %s en erreur (%s)", source, exc)
        if not self._source_failing.get(source):
            self._source_failing[source] = True
            self._log.record(new_observation(
                "error", source,  # type: ignore[arg-type]
                title=f"Veille news {source} : {type(exc).__name__}",
                detail={"error": str(exc)[:500], "type": type(exc).__name__},
            ))

    # ------------------------------------------------------------------ Yahoo
    def yahoo_ticker_for(self, symbol: str) -> str:
        if symbol in self._settings.crypto_bases and symbol not in self._settings.etoro_symbols:
            return f"{symbol}-USD"
        return yahoo_symbol_for(symbol)

    async def _fetch_yahoo(self, symbol: str) -> list[Observation]:
        resp = await self._client.get(
            YAHOO_RSS_URL, params={"s": self.yahoo_ticker_for(symbol)}, timeout=HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        out: list[Observation] = []
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            matched = self.match_symbol(title, preferred=symbol)
            if matched is None:
                continue  # titre sans lien avec un actif suivi
            published = None
            struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if struct:
                published = datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)
            obs = self._record_news("yahoo", title, entry.get("link"), published, matched,
                                    extra={"feed_symbol": symbol})
            if obs is not None:
                out.append(obs)
        return out

    async def _tick_yahoo(self) -> list[Observation]:
        out: list[Observation] = []
        failures = 0
        last_exc: BaseException | None = None
        symbols = self.watched_symbols
        for sym in symbols:
            try:
                out.extend(await self._fetch_yahoo(sym))
            except Exception as exc:  # noqa: BLE001 - un ticker en erreur n'arrête pas les autres
                failures += 1
                last_exc = exc
                log.debug("watch news yahoo %s : %s", sym, exc)
        if failures and failures == len(symbols) and last_exc is not None:
            self._note_source_error("yahoo", last_exc)
        else:
            self._source_failing["yahoo"] = False
        return out

    # ------------------------------------------------------------ CryptoPanic
    async def _tick_cryptopanic(self) -> list[Observation]:
        if not self._settings.cryptopanic_enabled:
            return []
        bases = self._settings.crypto_bases
        if not bases:
            return []
        params = {
            "auth_token": self._settings.cryptopanic_token,
            "currencies": ",".join(bases),
            "public": "true",
        }
        resp = await self._client.get(CRYPTOPANIC_URL, params=params, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        out: list[Observation] = []
        for post in (data.get("results") if isinstance(data, dict) else None) or []:
            title = (post.get("title") or "").strip()
            if not title:
                continue
            codes = [str(c.get("code", "")).upper() for c in post.get("currencies") or [] if isinstance(c, dict)]
            symbol = next((c for c in codes if c in self._patterns), None) or self.match_symbol(title)
            if symbol is None:
                continue
            published = _parse_iso(post.get("published_at"))
            origin = (post.get("source") or {}).get("title") if isinstance(post.get("source"), dict) else None
            obs = self._record_news("cryptopanic", title, post.get("url"), published, symbol,
                                    extra={"origin": origin, "currencies": codes})
            if obs is not None:
                out.append(obs)
        self._source_failing["cryptopanic"] = False
        return out

    # ------------------------------------------------------------------ boucle
    async def tick(self) -> list[Observation]:
        """Un cycle complet sur toutes les sources actives. Ne lève jamais."""
        news: list[Observation] = []
        if "yahoo" in self._settings.news_sources:
            try:
                news.extend(await self._tick_yahoo())
            except Exception as exc:  # noqa: BLE001
                self._note_source_error("yahoo", exc)
        if self._settings.cryptopanic_enabled:
            try:
                news.extend(await self._tick_cryptopanic())
            except Exception as exc:  # noqa: BLE001
                self._note_source_error("cryptopanic", exc)
        self.tick_count += 1
        self.last_tick_at = datetime.now(UTC)
        if news:
            log.info("watch news : %d nouvelle(s) observation(s)", len(news))
        return news

    async def run_forever(self, interval_s: float | None = None) -> None:
        interval = float(interval_s if interval_s is not None else self._settings.watch_news_interval_s)
        log.info("watch news : démarrage (intervalle %.0fs, sources %s)", interval, self.sources)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"tick: {exc}"
                log.exception("watch news : tick en erreur")
            await asyncio.sleep(interval)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
