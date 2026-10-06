"""Pydantic-схемы модуля `candidates` (минимум дня 6: `GET|PUT /candidates/me`).

Ответ `/candidates/me` — собственные данные кандидата, поэтому ПДн отдаются открыто;
в любых чужих выдачах (карточка, банк) используется только псевдоним — см. `matching`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.pii import normalize_phone

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TELEGRAM_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
MAX_TAGS = 50


def clean_text(value: str | None) -> str | None:
    """Trim + удаление управляющих символов; пустая строка → None."""
    if value is None:
        return None
    cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
    return cleaned or None


def normalize_tags(values: list[str] | None) -> list[str]:
    """Теги стека/ролей: trim, lower, без дублей, порядок сохранён, не более MAX_TAGS."""
    if not values:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        tag = (clean_text(raw) or "").lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result[:MAX_TAGS]


class PrivacyOut(BaseModel):
    searchable: bool = True
    show_city: bool = True
    show_fsp: bool = True
    show_accomplishments: bool = True
    hidden_company_ids: list[UUID] = Field(default_factory=list)


class PrivacyIn(BaseModel):
    """Настройки приватности в PUT /candidates/me: переданные ключи заменяют текущие,
    непереданные не меняются (частичное обновление, как и весь профиль)."""

    searchable: bool | None = None
    show_city: bool | None = None
    show_fsp: bool | None = None
    show_accomplishments: bool | None = None
    hidden_company_ids: list[UUID] | None = Field(default=None, max_length=200)

    def changes(self) -> dict[str, Any]:
        """Только явно переданные ключи; UUID — строками (JSONB-колонка privacy)."""
        data = self.model_dump(exclude_unset=True, exclude_none=True)
        if "hidden_company_ids" in data:
            data["hidden_company_ids"] = [str(v) for v in data["hidden_company_ids"]]
        return data


class CandidateMeOut(BaseModel):
    id: UUID
    pseudonym: str
    full_name: str | None
    phone: str | None
    telegram: str | None
    city: str | None
    industry_code: str | None
    specialization: str | None
    stack_tags_declared: list[str]
    stack_tags_confirmed: list[str]
    experience_years: int | None
    roles_declared: list[str]
    soft_skills_declared: list[str]
    about: str | None
    salary_floor: int | None
    privacy: PrivacyOut
    searchable: bool
    fsp_id: str | None
    fsp_linked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CategoryOut(BaseModel):
    """GET /candidates/me/category: категория = специализация × подтверждённый грейд.

    `grade_confirmed` всегда True: категория присваивается только из тестовой сессии
    (инвариант PLAN.md §3.7); поле — по контракту интегратора (фронт показывает бейдж).
    """

    specialization: str
    grade: str
    grade_confirmed: bool = True
    q_level: int
    computed_at: datetime


class SkillStateOut(BaseModel):
    """Узел персонального слоя графа навыков (подсветка «мой граф» на фронте).

    `state` по контракту графа: fresh — подтверждено и свежо, fading — тускнеет,
    expired — погасло, off — не активен (или специализация узла не найдена
    в справочнике). ПДн в ответе нет — слой можно отдавать фронту как есть.
    """

    node_id: str
    # код специализации — префикс node_id до точки («be.B1» → «be»)
    specialization_code: str
    active_level: int
    w_now: float
    state: Literal["fresh", "fading", "expired", "off"]


class SkillStatesOut(BaseModel):
    """GET /candidates/me/skill-states: состояния всех узлов кандидата поверх общего графа."""

    states: list[SkillStateOut]


class GraphSummaryOut(BaseModel):
    """GET /candidates/me/graph-summary: разбор графа навыков человеческим языком.

    `source`: llm — текст от модели; rules — детерминированный фолбэк без LLM
    (ключи не настроены или провайдер недоступен) — эндпоинт не падает в любом случае."""

    summary: str
    source: Literal["llm", "rules"]
    generated_at: datetime


class CandidateMeIn(BaseModel):
    """PUT /candidates/me: переданные поля заменяют текущие, непереданные не меняются.
    `privacy` обновляет настройки приватности (и производный флаг searchable профиля)."""

    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    telegram: str | None = Field(default=None, max_length=33)
    city: str | None = Field(default=None, max_length=120)
    industry_code: str | None = Field(default=None, max_length=64)
    # специализация кандидата: код из GET /skills/specializations (be, fe, …), null — сбросить
    specialization: str | None = Field(default=None, max_length=32)
    # списки: null равносилен пустому списку (колонки NOT NULL)
    stack_tags_declared: list[str] | None = None
    experience_years: int | None = Field(default=None, ge=0, le=60)
    roles_declared: list[str] | None = None
    soft_skills_declared: list[str] | None = None
    # «о себе»: хранится после санитайзера ПДн (контакты и ФИО → плейсхолдеры)
    about: str | None = Field(default=None, max_length=2000)
    salary_floor: int | None = Field(default=None, gt=0, le=100_000_000)
    # настройки приватности (переключатель «виден в банке» шлёт фронт); null — не менять
    privacy: PrivacyIn | None = None

    @field_validator("full_name", "city", "about", "industry_code", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("specialization", mode="after")
    @classmethod
    def _specialization(cls, value: str | None) -> str | None:
        cleaned = clean_text(value)
        return cleaned.lower() if cleaned else None

    @field_validator("phone", mode="after")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        digits = normalize_phone(value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("Телефон должен содержать от 10 до 15 цифр")
        return f"+{digits}"

    @field_validator("telegram", mode="after")
    @classmethod
    def _telegram(cls, value: str | None) -> str | None:
        value = clean_text(value)
        if value is None:
            return None
        value = value.lstrip("@")
        if not _TELEGRAM_RE.match(value):
            raise ValueError("Имя пользователя Telegram: 5–32 символа, латиница, цифры и _")
        return value

    @field_validator("stack_tags_declared", "roles_declared", "soft_skills_declared", mode="after")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str]:
        # колонки NOT NULL: явный null в JSON означает «очистить список», а не NULL в БД
        return normalize_tags(value)

    def changes(self) -> dict[str, Any]:
        """Только явно переданные поля (семантика частичного PUT); privacy — словарём
        переданных ключей (UUID — строками)."""
        data = self.model_dump(exclude_unset=True)
        if data.get("privacy") is not None and self.privacy is not None:
            data["privacy"] = self.privacy.changes()
        elif "privacy" in data and data["privacy"] is None:
            data.pop("privacy")  # явный null — «не менять», а не очистка настроек
        return data
