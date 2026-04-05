from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

# Chemin absolu → trouve toujours le .env peu importe depuis où uvicorn est lancé
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = ""

    # Kraken
    kraken_api_key: str = ""
    kraken_secret_key: str = ""

    # Binance
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"

    # Anthropic
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    whatsapp_recipient: str = ""

    # CoinGecko
    coingecko_api_key: str = ""

    # Etherscan (plan gratuit : 100k req/jour)
    etherscan_api_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Trading
    paper_trading: bool = True
    total_capital_usd: float = 1000.0
    base_position_size_pct: float = 2.0
    max_position_size_pct: float = 5.0
    stop_loss_pct: float = 7.0
    drawdown_disable_pct: float = 12.0
    max_consecutive_losses: int = 3
    volatility_high_threshold: float = 0.05

    # App
    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()