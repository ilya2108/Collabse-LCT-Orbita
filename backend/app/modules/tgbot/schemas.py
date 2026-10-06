"""Pydantic-схемы модуля tgbot (привязка Telegram и внутренние ручки дайджеста)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class LinkTokenIn(BaseModel):
    """Роль привязки; по умолчанию — активная роль пользователя."""

    role: Literal["candidate", "employer"] | None = None


class LinkTokenOut(BaseModel):
    """Одноразовый код привязки: показывается один раз, в БД хранится только HMAC."""

    code: str
    deep_link: str
    role: Literal["candidate", "employer"]
    expires_at: datetime


class LinkStatusOut(BaseModel):
    """Статус привязки Telegram текущего пользователя (для блока в настройках)."""

    linked: bool
    username_masked: str | None = None
    linked_at: datetime | None = None
    digest_enabled: bool = False


class DigestRunOut(BaseModel):
    """Результат немедленной рассылки дайджеста всем привязанным чатам."""

    sent: int
    mode: Literal["stub", "polling"]


class OutboxItemOut(BaseModel):
    """Строка журнала исходящих сообщений бота (ПДн в тексте уже замаскированы)."""

    id: int
    tg_chat_id: int
    kind: str
    text: str
    status: str
    created_at: datetime


class OutboxOut(BaseModel):
    items: list[OutboxItemOut]
    total: int
