"""Pydantic-схемы модуля `tma` (Telegram Mini App).

Деловые ручки переиспользуют схемы `matching`/`employers` — мини-апп видит ровно
те же контракты, что и веб; здесь только «кто я» и локальные входные обёртки.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.modules.employers.schemas import NeedOut


class TmaMeOut(BaseModel):
    """GET /tma/me: кто пришёл из мини-аппа; набор полей зависит от вида актора."""

    kind: Literal["candidate", "employer", "member"]
    # кандидат: псевдоним и подтверждённый грейд (null — профиля/категории ещё нет)
    pseudonym: str | None = None
    grade: str | None = None
    # employer | member: компания и роль в ней (owner | hr | hiring_manager)
    company_name: str | None = None
    role: str | None = None


class TmaDeclineIn(BaseModel):
    """POST /tma/invitations/{id}/decline: причина отказа — по желанию."""

    reason_code: Literal["salary", "not_relevant", "busy", "other"] | None = None


class TmaNeedsOut(BaseModel):
    """GET /tma/needs: активные потребности компании актора."""

    items: list[NeedOut]
    total: int
