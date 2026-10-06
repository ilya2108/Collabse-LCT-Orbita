"""Пороги сигналов целостности — отдельный pydantic-settings, чтобы не трогать `core/config`.

Читает тот же `.env`, что и основное приложение (extra="ignore" в обоих классах —
переменные друг друга не мешают). Калибровка порогов — позже (по данным пилота):
значения намеренно вынесены в env, формула агрегата остаётся простой и честной
(`integrity.compute_integrity`).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntegritySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # вставка из буфера длиной >= порога считается «крупной» (large_paste)
    integrity_paste_len: int = Field(default=200, ge=1)
    # уход со вкладки (blur → focus) на >= порога секунд (tab_blur)
    integrity_blur_seconds: int = Field(default=5, ge=1)
    # ответ на задание быстрее порога секунд — подозрительно быстрый (fast_answer)
    integrity_min_seconds_per_item: int = Field(default=20, ge=1)
    # ответ от этой длины без единого набранного символа — «появился целиком» (no_edit)
    integrity_no_edit_min_len: int = Field(default=120, ge=1)
    # вместимость одного батча телеметрии; превышение — 413
    integrity_max_batch_events: int = Field(default=500, ge=1)


@lru_cache
def get_integrity_settings() -> IntegritySettings:
    return IntegritySettings()
