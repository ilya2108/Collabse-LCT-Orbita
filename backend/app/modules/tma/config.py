"""Настройки Telegram Mini App — отдельный pydantic-settings по образцу `tgbot/config.py`.

Читает тот же `.env`, что и основное приложение (`extra="ignore"` — переменные других
классов настроек не мешают). Единственный параметр — срок годности `initData`: сама
подпись Telegram бессрочна, поэтому поле `auth_date` сверяется с текущим временем.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TmaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # initData старше этого срока (секунды от auth_date) отклоняется:
    # перехваченную строку нельзя переиспользовать бесконечно
    tma_init_data_ttl_seconds: int = Field(default=3600, ge=1)


@lru_cache
def get_tma_settings() -> TmaSettings:
    return TmaSettings()
