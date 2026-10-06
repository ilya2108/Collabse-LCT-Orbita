"""Настройки телеграм-бота — отдельный pydantic-settings, чтобы не трогать `core/config`.

Читает тот же `.env`, что и основное приложение (extra="ignore" в обоих классах —
переменные друг друга не мешают). Режимы:
- `TELEGRAM_BOT_MODE=stub` (по умолчанию): без сети и без токена — исходящие сообщения
  пишутся только в таблицу `tg_outbox` (compose и тесты работают без ключей);
- `TELEGRAM_BOT_MODE=polling`: реальный бот, требуется `TELEGRAM_BOT_TOKEN`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TgBotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_mode: Literal["stub", "polling"] = "stub"
    telegram_bot_token: str = ""
    # username бота без @ — для deep-link вида https://t.me/<бот>?start=<код>
    telegram_bot_username: str = "OrbitaFspBot"
    # недельный дайджест: день недели (0 — понедельник … 6 — воскресенье) и час (UTC)
    digest_cron_weekday: int = Field(default=0, ge=0, le=6)
    digest_cron_hour: int = Field(default=9, ge=0, le=23)
    # период фоновых проверок бота (пуши о новых приглашениях, тики планировщика)
    tg_notify_interval_seconds: int = Field(default=30, ge=1)
    # срок жизни одноразового кода привязки
    tg_link_token_ttl_minutes: int = Field(default=15, ge=1)

    @model_validator(mode="after")
    def _validate(self) -> TgBotSettings:
        if self.telegram_bot_mode == "polling" and not self.telegram_bot_token.strip():
            raise ValueError(
                "TELEGRAM_BOT_MODE=polling требует TELEGRAM_BOT_TOKEN (возьмите у @BotFather)"
            )
        return self


@lru_cache
def get_tgbot_settings() -> TgBotSettings:
    return TgBotSettings()
