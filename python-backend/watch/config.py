"""Configuration de la couche de veille : variables d'environnement WATCH_* (Railway).

La veille est désactivée par défaut (`WATCH_ENABLED=false`) : CryptoMind tourne exactement
comme avant tant que la variable n'est pas posée à `true`.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Miroir de `WATCHED_PAIRS` (main.py). Dupliqué ici pour ne pas importer main.py
# (qui tire ccxt / redis / la base de données) depuis un simple module de config.
DEFAULT_KRAKEN_PAIRS = (
    "BTCUSD,ETHUSD,SOLUSD,XRPUSD,ADAUSD,DOTUSD,LINKUSD,AVAXUSD,ATOMUSD,NEARUSD,ALGOUSD,LTCUSD"
)
DEFAULT_ETORO_SYMBOLS = "AAPL,MSFT,NVDA,AMZN,GOOGL,META,NEM,AEM"
DEFAULT_NEWS_SOURCES = "yahoo,cryptopanic"
KNOWN_NEWS_SOURCES = frozenset({"yahoo", "cryptopanic"})


def _csv_upper(value: str) -> str:
    return ",".join(s.strip().upper() for s in value.split(",") if s.strip())


class WatchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    watch_enabled: bool = Field(default=False, alias="WATCH_ENABLED")
    watch_interval_s: float = Field(default=300.0, ge=1, alias="WATCH_INTERVAL_S")
    watch_move_threshold_pct: float = Field(default=2.0, gt=0, alias="WATCH_MOVE_THRESHOLD_PCT")
    watch_kraken_pairs: str = Field(default=DEFAULT_KRAKEN_PAIRS, alias="WATCH_KRAKEN_PAIRS")
    watch_etoro_symbols: str = Field(default=DEFAULT_ETORO_SYMBOLS, alias="WATCH_ETORO_SYMBOLS")
    # Small caps crypto (top 50-100 par capitalisation, cotées sur Kraken) : observation seule
    watch_smallcaps_enabled: bool = Field(default=True, alias="WATCH_SMALLCAPS_ENABLED")
    watch_smallcaps_rank_from: int = Field(default=51, ge=1, alias="WATCH_SMALLCAPS_RANK_FROM")
    watch_smallcaps_rank_to: int = Field(default=100, ge=1, alias="WATCH_SMALLCAPS_RANK_TO")
    watch_news_interval_s: float = Field(default=900.0, ge=1, alias="WATCH_NEWS_INTERVAL_S")
    watch_news_sources: str = Field(default=DEFAULT_NEWS_SOURCES, alias="WATCH_NEWS_SOURCES")
    cryptopanic_token: str | None = Field(default=None, alias="CRYPTOPANIC_TOKEN")
    watch_log_path: str | None = Field(default="/data/observations.jsonl", alias="WATCH_LOG_PATH")
    watch_max_memory: int = Field(default=1000, ge=1, alias="WATCH_MAX_MEMORY")
    # Règles / alertes (PR 4). WATCH_DATABASE_URL vide = `app.database.engine` (import tardif).
    watch_database_url: str | None = Field(default=None, alias="WATCH_DATABASE_URL")
    watch_rules_interval_s: float = Field(default=300.0, ge=1, alias="WATCH_RULES_INTERVAL_S")
    watch_fng_enabled: bool = Field(default=True, alias="WATCH_FNG_ENABLED")

    @field_validator("watch_database_url")
    @classmethod
    def _empty_url_is_none(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    @field_validator("watch_kraken_pairs", "watch_etoro_symbols")
    @classmethod
    def _strip_csv(cls, v: str) -> str:
        return _csv_upper(v)

    @field_validator("watch_news_sources")
    @classmethod
    def _strip_sources(cls, v: str) -> str:
        return ",".join(s.strip().lower() for s in v.split(",") if s.strip())

    @field_validator("cryptopanic_token")
    @classmethod
    def _empty_token_is_none(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    # ------------------------------------------------------------ dérivés
    @property
    def kraken_pairs(self) -> list[str]:
        return [s for s in self.watch_kraken_pairs.split(",") if s]

    @property
    def etoro_symbols(self) -> list[str]:
        return [s for s in self.watch_etoro_symbols.split(",") if s]

    @property
    def news_sources(self) -> list[str]:
        return [s for s in self.watch_news_sources.split(",") if s in KNOWN_NEWS_SOURCES]

    @property
    def crypto_bases(self) -> list[str]:
        """BTCUSD -> BTC (base des paires Kraken, pour les news crypto)."""
        out: list[str] = []
        for pair in self.kraken_pairs:
            base = pair[:-3] if pair.endswith(("USD", "EUR")) and len(pair) > 3 else pair
            if base and base not in out:
                out.append(base)
        return out

    @property
    def cryptopanic_enabled(self) -> bool:
        return "cryptopanic" in self.news_sources and self.cryptopanic_token is not None


@lru_cache
def get_settings() -> WatchSettings:
    return WatchSettings()
