"""Configuration du module eToro : tout vient des variables d'environnement (Railway).

Aucun secret n'est stocké dans le code. En mode `real`, le module refuse de trader tant que
`ETORO_CREDENTIALS_ROTATED=true` n'est pas posé (étape bloquante : révocation des identifiants
complets et passage à une clé API scopée à l'Agent Portfolio).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- eToro ---
    etoro_api_key: str = Field(default="", alias="ETORO_API_KEY")
    etoro_user_key: str | None = Field(default=None, alias="ETORO_USER_KEY")
    etoro_portfolio_id: str | None = Field(default=None, alias="ETORO_PORTFOLIO_ID")
    etoro_mode: Literal["demo", "real"] = Field(
        default="demo", validation_alias=AliasChoices("ETORO_TRADING_MODE", "ETORO_MODE")
    )  # ETORO_TRADING_MODE est le nom officiel, ETORO_MODE reste accepté
    etoro_credentials_rotated: bool = Field(default=False, alias="ETORO_CREDENTIALS_ROTATED")
    etoro_base_url_demo: str = Field(default="https://public-api.etoro.com", alias="ETORO_BASE_URL_DEMO")
    etoro_base_url_real: str = Field(default="https://public-api.etoro.com", alias="ETORO_BASE_URL_REAL")
    etoro_timeout_s: float = Field(default=10.0, alias="ETORO_TIMEOUT_S")

    # --- Interrupteur d'urgence ---
    etoro_agent_enabled: bool = Field(default=True, alias="ETORO_AGENT_ENABLED")
    etoro_kill_switch_token: str | None = Field(default=None, alias="ETORO_KILL_SWITCH_TOKEN")

    # --- Garde-fous de risque ---
    etoro_max_open_positions: int = Field(default=3, ge=1, le=5, alias="ETORO_MAX_OPEN_POSITIONS")
    etoro_cooldown_hours: float = Field(default=4.0, ge=0, alias="ETORO_COOLDOWN_HOURS")
    etoro_daily_loss_limit_pct: float = Field(default=3.0, gt=0, alias="ETORO_DAILY_LOSS_LIMIT_PCT")
    etoro_breaker_pause_hours: float = Field(default=24.0, gt=0, alias="ETORO_BREAKER_PAUSE_HOURS")
    etoro_min_score: float = Field(default=70.0, ge=0, le=100, alias="ETORO_MIN_SCORE")
    etoro_sl_pct: float = Field(default=2.0, gt=0, alias="ETORO_SL_PCT")
    etoro_tp_pct: float = Field(default=4.0, gt=0, alias="ETORO_TP_PCT")
    etoro_position_size_pct: float = Field(default=10.0, gt=0, le=25, alias="ETORO_POSITION_SIZE_PCT")
    etoro_max_leverage: int = Field(default=1, ge=1, le=5, alias="ETORO_MAX_LEVERAGE")

    # --- Univers restreint ---
    # Vérifié le 2026-09-06 via le connecteur eToro : sur ce compte (Belgique), l'or CFD (GOLD, id 18)
    # et tous les ETF américains (SPY, GLD, IAU, VOO...) sont non ouvrables. L'exposition or passe par
    # les minières Newmont (NEM) et Agnico Eagle (AEM), toutes deux ouvrables.
    # Crypto en démo eToro : TEMPORAIRE (détention réelle, levier 1). Dès la fusion Kraken + eToro, BTC/ETH/SOL/HYPE
    # basculent sur Kraken et la permission Crypto est retirée de la clé eToro (voir docs/checklist_demo_vers_reel.md).
    etoro_universe: str = Field(default="AAPL,MSFT,NVDA,AMZN,GOOGL,META,NEM,AEM,BTC,ETH,SOL,HYPE", alias="ETORO_UNIVERSE")
    # Symboles marqués « actif expérimental » par eToro lui-même : label conservé dans les logs et le dashboard
    etoro_experimental_symbols: str = Field(default="HYPE", alias="ETORO_EXPERIMENTAL_SYMBOLS")

    # --- Signaux de confirmation ---
    use_rankings_confirmation: bool = Field(default=True, alias="ETORO_USE_RANKINGS")
    rankings_top_n: int = Field(default=20, ge=1, le=100, alias="ETORO_RANKINGS_TOP_N")
    rankings_max_drawdown_pct: float = Field(default=15.0, alias="ETORO_RANKINGS_MAX_DD_PCT")
    rankings_min_profitable_months_pct: float = Field(default=60.0, alias="ETORO_RANKINGS_MIN_PROFITABLE_MONTHS_PCT")
    rankings_min_confirmation: float = Field(default=0.3, ge=0, le=1, alias="ETORO_RANKINGS_MIN_CONFIRMATION")
    use_news_sentiment: bool = Field(default=True, alias="ETORO_USE_NEWS")
    news_api_key: str | None = Field(default=None, alias="NEWS_API_KEY")
    news_min_sentiment: float = Field(default=-0.5, ge=-1, le=1, alias="ETORO_NEWS_MIN_SENTIMENT")

    # --- Boucle de l'agent dans le process API (hébergeur à service unique : Render) ---
    etoro_run_agent: bool = Field(default=False, alias="ETORO_RUN_AGENT")
    etoro_cycle_interval_s: int = Field(default=300, ge=30, alias="ETORO_CYCLE_INTERVAL_S")

    # --- Persistance ---
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_service_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_KEY")
    etoro_state_path: str = Field(default="/data/etoro_state.json", alias="ETORO_STATE_PATH")
    etoro_decisions_path: str = Field(default="/data/etoro_decisions.jsonl", alias="ETORO_DECISIONS_PATH")


    @field_validator("etoro_universe")
    @classmethod
    def _strip_universe(cls, v: str) -> str:
        return ",".join(s.strip().upper() for s in v.split(",") if s.strip())

    @property
    def universe(self) -> list[str]:
        return [s for s in self.etoro_universe.split(",") if s]

    @property
    def experimental_symbols(self) -> list[str]:
        return [x.strip().upper() for x in self.etoro_experimental_symbols.split(",") if x.strip()]

    @property
    def base_url(self) -> str:
        return self.etoro_base_url_real if self.etoro_mode == "real" else self.etoro_base_url_demo

    @property
    def real_mode_locked(self) -> bool:
        """True si on est en mode real sans rotation des identifiants : trading interdit."""
        return self.etoro_mode == "real" and not self.etoro_credentials_rotated


@lru_cache
def get_settings() -> Settings:
    return Settings()
