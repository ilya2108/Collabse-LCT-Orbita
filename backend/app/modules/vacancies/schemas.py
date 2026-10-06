"""Pydantic-схемы модуля `vacancies`.

Публичная карточка вакансии без ПДн: название компании — публичное (синтетика сида),
описание отдаётся коротким фрагментом. `need_nodes_count` — размер подграфа связанной
потребности (объяснимость: отклик придёт с покрытием нужных узлов).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class VacancyPublicOut(BaseModel):
    """Карточка в публичном списке `GET /vacancies`."""

    id: UUID
    title: str
    company_name: str
    specialization_code: str | None
    grade: str | None
    salary_from: int
    salary_to: int
    format: str | None
    description: str | None
    need_nodes_count: int | None
    applied: bool


class VacancyListOut(BaseModel):
    items: list[VacancyPublicOut]
    total: int


class VacancyMineOut(BaseModel):
    """Карточка в списке вакансий моей компании `GET /vacancies/mine`."""

    id: UUID
    title: str
    specialization_code: str | None
    grade: str | None
    salary_from: int
    salary_to: int
    format: str | None
    status: str
    description: str | None
    need_nodes_count: int | None
    applications_count: int
    published_at: datetime | None


class VacancyMineListOut(BaseModel):
    items: list[VacancyMineOut]
    total: int


class ApplicationOut(BaseModel):
    """Отклик кандидата (повторный `apply` возвращает тот же отклик)."""

    id: UUID
    vacancy_id: UUID
    status: str
    sent_at: datetime
