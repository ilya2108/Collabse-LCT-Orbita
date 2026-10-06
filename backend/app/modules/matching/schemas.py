"""Pydantic-схемы модуля `matching`: ранжированная выдача по потребности, мини-CRM
(pipeline) и приглашения (PLAN.md §5.3, §5.6).

Правило приватности: кандидат в любой выдаче работодателя — только псевдоним «B-7F3A»
и маскированные поля; открытые контакты отдаёт исключительно `GET /invitations/{id}/contacts`
при наличии `contact_grant` (после принятия приглашения кандидатом).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.candidates.schemas import clean_text

# --- подбор -----------------------------------------------------------------------------


class ExplanationOut(BaseModel):
    """Объяснимость (§5.4): из чего сложилась позиция кандидата в выдаче."""

    basis: Literal["subgraph", "grade_profile", "profile_strength"]
    matched_nodes: list[str]
    partial_nodes: list[str]
    missing_nodes: list[str]
    text: str


class RankedCandidateOut(BaseModel):
    candidate_id: UUID
    pseudonym: str
    score: float
    coverage: float | None
    grade: str
    # грейд подтверждён тестом и совпадает с запрошенным; иначе кандидат ниже в выдаче
    grade_confirmed: bool
    q_level: int
    shape: str
    # целостность последней завершённой сессии теста; null — данных нет (телеметрии не было)
    integrity_level: Literal["high", "medium", "low"] | None = None
    explanation: ExplanationOut


class NeedCandidatesOut(BaseModel):
    need_id: UUID
    basis: Literal["subgraph", "grade_profile", "profile_strength"]
    items: list[RankedCandidateOut]
    total: int


class SelectionSummaryOut(BaseModel):
    """GET /employers/needs/{id}/selection-summary: картина по кандидатам человеческим языком.

    `source`: llm — текст от модели; rules — детерминированный фолбэк без LLM
    (ключи не настроены или провайдер недоступен) — эндпоинт не падает в любом случае."""

    summary: str
    source: Literal["llm", "rules"]
    generated_at: datetime


# --- банк кандидатов ----------------------------------------------------------------------


class BankCandidateOut(BaseModel):
    """Карточка банка кандидатов: псевдоним и агрегаты категории, без ПДн и контактов."""

    candidate_id: UUID
    pseudonym: str
    specialization_code: str
    grade: str
    q_level: int
    shape: str
    # дата присвоения категории (computed_at) — «грейд подтверждён тогда-то»
    grade_confirmed_at: datetime
    # подтверждённый заданиями стек (первые 8 тегов)
    stack_confirmed: list[str]


class BankOut(BaseModel):
    """GET /matching/bank: страница банка в конверте пагинации (items/total/limit/offset)."""

    items: list[BankCandidateOut]
    total: int
    limit: int
    offset: int


# --- мини-CRM ---------------------------------------------------------------------------


class PipelineItemOut(BaseModel):
    candidate_id: UUID
    pseudonym: str
    stage: str
    note: str | None
    invitation_id: UUID | None
    invitation_status: str | None
    # контакты доступны только после принятия приглашения (стадии accepted и дальше)
    contacts_available: bool
    full_name_masked: str | None
    score: float | None
    # целостность последней завершённой сессии теста; null — данных нет (телеметрии не было)
    integrity_level: Literal["high", "medium", "low"] | None = None
    updated_at: datetime


class PipelineColumnOut(BaseModel):
    stage: str
    items: list[PipelineItemOut]


class PipelineOut(BaseModel):
    need_id: UUID
    columns: list[PipelineColumnOut]
    total: int


class PipelineAddIn(BaseModel):
    """Добавление кандидата в шорт-лист потребности."""

    candidate_id: UUID
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)


class PipelineStageIn(BaseModel):
    """Ручной перевод стадии; `invited`/`accepted` проставляет только сервис приглашений."""

    stage: Literal["shortlist", "interview", "offer", "hired", "declined"]
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)


# --- приглашения ------------------------------------------------------------------------


class InvitationCreateIn(BaseModel):
    """POST /invitations: зарплата «от–до» обязательна (правило ТЗ)."""

    candidate_id: UUID
    need_id: UUID | None = None
    title: str = Field(default="Приглашение к сотрудничеству", min_length=1, max_length=300)
    message: str | None = Field(default=None, max_length=4000)
    salary_from: int = Field(gt=0, le=100_000_000)
    salary_to: int = Field(gt=0, le=100_000_000)
    contact_method: str | None = Field(default=None, max_length=300)

    @field_validator("title", "message", "contact_method", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)

    @model_validator(mode="after")
    def _salary(self) -> InvitationCreateIn:
        if self.salary_from > self.salary_to:
            raise ValueError("Зарплата «от» не может быть больше зарплаты «до»")
        return self


class InvitationDeclineIn(BaseModel):
    reason_code: Literal["salary", "not_relevant", "busy", "other"] | None = None
    text: str | None = Field(default=None, max_length=2000)

    @field_validator("text", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_text(value)


class InvitationOut(BaseModel):
    """Приглашение; поля другой стороны заполняются по роли запрашивающего:
    работодатель видит псевдоним кандидата, кандидат — название компании."""

    id: UUID
    need_id: UUID | None
    candidate_pseudonym: str | None = None
    company_name: str | None = None
    title: str
    message: str | None
    salary_from: int
    salary_to: int
    contact_method: str | None = None
    explanation: dict[str, Any]
    status: str
    sent_at: datetime
    viewed_at: datetime | None
    decided_at: datetime | None
    expires_at: datetime
    decline_reason_code: str | None
    decline_text: str | None


class InvitationCreateOut(BaseModel):
    invitation: InvitationOut
    # «до» ниже приватной зарплатной планки кандидата (без раскрытия суммы, §5.6 п. 3)
    below_salary_floor: bool


class InvitationsOut(BaseModel):
    items: list[InvitationOut]
    total: int


class CandidateContactsOut(BaseModel):
    """Контакты кандидата для работодателя — только через contact_grant, с аудитом."""

    pseudonym: str
    full_name: str | None
    email: str | None
    phone: str | None
    telegram: str | None


class CompanyContactsOut(BaseModel):
    """Контакты пригласившей компании — кандидату после принятия приглашения."""

    company_name: str
    contact_method: str | None
    contact_email: str | None
    contact_phone: str | None


class InvitationAcceptOut(BaseModel):
    """Ответ на принятие: приглашение + контакты компании (раскрываются кандидату)."""

    invitation: InvitationOut
    company_contacts: CompanyContactsOut
