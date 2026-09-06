"""Configuration Telegram : deux chats séparés (veille / trading), restriction stricte au chat_id."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_enabled: bool = Field(default=False, alias="TELEGRAM_ENABLED")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id_watch: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID_WATCH")
    telegram_chat_id_trading: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID_TRADING")
    telegram_webhook_secret: str | None = Field(default=None, alias="TELEGRAM_WEBHOOK_SECRET")
    telegram_public_url: str | None = Field(default=None, alias="TELEGRAM_PUBLIC_URL")
    telegram_api_base: str = Field(default="https://api.telegram.org", alias="TELEGRAM_API_BASE")
    telegram_max_per_hour: int = Field(default=30, ge=1, alias="TELEGRAM_MAX_PER_HOUR")

    @property
    def configured(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_bot_token
                    and (self.telegram_chat_id_watch or self.telegram_chat_id_trading))

    @property
    def allowed_chat_ids(self) -> set[str]:
        return {str(c) for c in (self.telegram_chat_id_watch, self.telegram_chat_id_trading) if c}

    def chat_id(self, chat: str) -> str | None:
        if chat == "watch":
            return self.telegram_chat_id_watch or self.telegram_chat_id_trading
        return self.telegram_chat_id_trading or self.telegram_chat_id_watch


@lru_cache
def get_telegram_settings() -> TelegramSettings:
    return TelegramSettings()
