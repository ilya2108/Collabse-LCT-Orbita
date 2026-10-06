"""Pydantic-схемы модуля `auth` (контракт CLAUDE.md «Контракты API»)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SelectableRole = Literal["candidate", "employer"]
ActiveRole = Literal["candidate", "employer", "admin"]


class ConsentOut(BaseModel):
    pdn_at: datetime | None = None
    policy_version: str | None = None
    publish_at: datetime | None = None


class MeResponse(BaseModel):
    id: UUID
    email: str | None
    email_verified: bool
    roles: list[str]
    active_role: ActiveRole | None
    consent: ConsentOut
    created_at: datetime
    # расширение контракта: строки прав для скрытия разделов UI (см. permissions.py)
    permissions: list[str]


class RoleIn(BaseModel):
    role: SelectableRole


class ConsentIn(BaseModel):
    # версия политики обработки ПДн, текст которой показан за чекбоксом
    policy_version: str = Field(min_length=1, max_length=64)
    # согласие на показ профиля работодателям (без него кандидат не searchable)
    publish: bool = False
