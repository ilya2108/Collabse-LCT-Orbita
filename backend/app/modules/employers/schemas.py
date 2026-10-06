"""Pydantic-схемы модуля `employers`: компания (личный/корпоративный профиль),
участники и приглашения в команду, потребности (минимальный CRUD PLAN.md §7.5).

ПДн участников (ФИО, email, telegram) наружу отдаются только маскированными
(`mask_email` и т. п.): список участников видят все члены компании, а открытые значения
не нужны ни одному сценарию UI. В БД они лежат шифртекстом (`EncryptedStr` + blind index).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.pii import normalize_phone
from app.modules.candidates.schemas import clean_text, normalize_tags

GRADES = ("junior", "middle", "senior")


class CompanyOut(BaseModel):
    id: UUID
    kind: Literal["personal", "corporate"]
    name: str
    inn: str | None
    description: str | None
    industry_code: str | None
    website: str | None
    # контакты компании — свои данные работодателя, отдаются открыто (как /candidates/me)
    contact_email: str | None
    contact_phone: str | None
    # роль текущего пользователя в компании: owner | hr | hiring_manager
    my_role: str
    members_count: int | None = None
    created_at: datetime
    updated_at: datetime


class CompanyUpdateIn(BaseModel):
    """PUT /employers/me/company: переданные поля заменяют текущие (частичный PUT)."""

    name: str | None = Field(default=None, min_length=1, max_length=300)
    kind: Literal["personal", "corporate"] | None = None
    inn: str | None = Field(default=None, max_length=12)
    description: str | None = Field(default=None, max_length=4000)
    industry_code: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=300)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=32)

    @field_validator("name", "description", "website", "industry_code", "inn", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("inn", mode="after")
    @classmethod
    def _inn(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isdigit() or len(value) not in (10, 12):
            raise ValueError("ИНН — 10 или 12 цифр")
        return value

    @field_validator("contact_email", mode="after")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Некорректный email")
        return value.lower()

    @field_validator("contact_phone", mode="after")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        digits = normalize_phone(value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("Телефон должен содержать от 10 до 15 цифр")
        return f"+{digits}"

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class MemberOut(BaseModel):
    """Участник компании: ПДн приглашённого — только маскированные значения."""

    id: UUID
    role: Literal["owner", "hr", "hiring_manager"]
    status: Literal["invited", "active", "revoked"]
    # маскированные ПДн: «И***в И.И.», «h***r@fsp.local», телефон/телеграм по аналогии
    full_name_masked: str | None
    email_masked: str | None
    telegram_masked: str | None
    invite_channel: Literal["email", "telegram"] | None
    has_account: bool
    invited_at: datetime | None
    accepted_at: datetime | None


class MembersOut(BaseModel):
    items: list[MemberOut]
    total: int


class MemberInviteIn(BaseModel):
    """Приглашение участника: контакт обязателен в выбранном канале."""

    role: Literal["hr", "hiring_manager"]
    channel: Literal["email", "telegram"]
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    telegram: str | None = Field(default=None, max_length=33)

    @field_validator("full_name", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("email", mode="after")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Некорректный email")
        return value.lower()

    @field_validator("telegram", mode="after")
    @classmethod
    def _telegram(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        return value.lstrip("@")

    @model_validator(mode="after")
    def _contact_for_channel(self) -> MemberInviteIn:
        if self.channel == "email" and not self.email:
            raise ValueError("Для канала email укажите email приглашаемого")
        if self.channel == "telegram" and not self.telegram:
            raise ValueError("Для канала telegram укажите имя пользователя Telegram")
        return self


class MemberInviteOut(BaseModel):
    """Ответ на приглашение: одноразовый токен показывается ровно один раз."""

    member: MemberOut
    invite_token: str
    invite_url: str


class InvitePublicOut(BaseModel):
    """Публичная информация по invite-токену (для страницы/бота принятия)."""

    company_name: str
    role: Literal["hr", "hiring_manager"]
    status: Literal["invited", "active", "revoked"]


class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=8, max_length=128)


class NeedIn(BaseModel):
    """POST /needs: зарплата «от–до» обязательна (PLAN.md §5.1), from ≤ to, > 0."""

    title: str = Field(min_length=1, max_length=300)
    raw_text: str | None = Field(default=None, max_length=8000)
    first_months_tasks: str | None = Field(default=None, max_length=4000)
    specialization: str = Field(min_length=1, max_length=16)
    grade: Literal["junior", "middle", "senior"]
    stack_tags: list[str] | None = None
    industry_code: str | None = Field(default=None, max_length=64)
    salary_from: int = Field(gt=0, le=100_000_000)
    salary_to: int = Field(gt=0, le=100_000_000)
    format: Literal["remote", "office", "hybrid"] | None = None

    @field_validator("title", "raw_text", "first_months_tasks", "industry_code", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("stack_tags", mode="after")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str]:
        return normalize_tags(value)

    @model_validator(mode="after")
    def _salary(self) -> NeedIn:
        if self.salary_from > self.salary_to:
            raise ValueError("Зарплата «от» не может быть больше зарплаты «до»")
        return self


class NeedUpdateIn(BaseModel):
    """PUT /needs/{id}: частичное обновление; зарплата проверяется сервисом по итогу."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    raw_text: str | None = Field(default=None, max_length=8000)
    first_months_tasks: str | None = Field(default=None, max_length=4000)
    grade: Literal["junior", "middle", "senior"] | None = None
    stack_tags: list[str] | None = None
    industry_code: str | None = Field(default=None, max_length=64)
    salary_from: int | None = Field(default=None, gt=0, le=100_000_000)
    salary_to: int | None = Field(default=None, gt=0, le=100_000_000)
    format: Literal["remote", "office", "hybrid"] | None = None
    status: Literal["draft", "active", "archived"] | None = None

    @field_validator("title", "raw_text", "first_months_tasks", "industry_code", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("stack_tags", mode="after")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str]:
        return normalize_tags(value)

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class NeedOut(BaseModel):
    id: UUID
    company_id: UUID
    title: str
    raw_text: str | None
    first_months_tasks: str | None
    specialization: str | None
    grade: str | None
    stack_tags: list[str]
    industry_code: str | None
    salary_from: int | None
    salary_to: int | None
    format: str | None
    status: str
    created_at: datetime
    updated_at: datetime
