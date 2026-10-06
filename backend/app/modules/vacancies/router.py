"""Роутер модуля `vacancies` (префикс `/vacancies`).

- `GET /vacancies` — публичный список опубликованных вакансий: доступен кандидату
  и работодателю (ПДн нет, название компании — публичное поле);
- `POST /vacancies/{id}/apply` — отклик кандидата (идемпотентен, 404 на неопубликованную);
- `GET /vacancies/mine` — вакансии компании текущего работодателя + счётчики откликов
  (членство — как в module employers: своя компания или активное участие).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import (
    ROLE_ADMIN,
    ROLE_CANDIDATE,
    ROLE_EMPLOYER,
    require_roles,
)
from app.models.user import AppUser
from app.modules.employers.service import get_or_create_company
from app.modules.vacancies import service
from app.modules.vacancies.schemas import (
    ApplicationOut,
    VacancyListOut,
    VacancyMineListOut,
)

router = APIRouter(prefix="/vacancies", tags=["vacancies"])

AnyRoleUser = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_EMPLOYER, ROLE_ADMIN))]
CandidateUser = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_ADMIN))]
EmployerUser = Annotated[AppUser, Depends(require_roles(ROLE_EMPLOYER, ROLE_ADMIN))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "",
    response_model=VacancyListOut,
    summary="Опубликованные вакансии (кандидату — с флагом «уже откликался»)",
)
async def list_vacancies(user: AnyRoleUser, session: Session) -> VacancyListOut:
    items = await service.list_published(session, user)
    return VacancyListOut(items=items, total=len(items))


@router.get(
    "/mine",
    response_model=VacancyMineListOut,
    summary="Вакансии моей компании со счётчиком откликов",
)
async def list_my_vacancies(user: EmployerUser, session: Session) -> VacancyMineListOut:
    company, _ = await get_or_create_company(session, user)
    items = await service.list_company_vacancies(session, company)
    return VacancyMineListOut(items=items, total=len(items))


@router.post(
    "/{vacancy_id}/apply",
    response_model=ApplicationOut,
    summary="Откликнуться на вакансию (идемпотентно: повтор вернёт тот же отклик)",
    responses={404: {"description": "Вакансия не найдена или не опубликована"}},
)
async def apply(vacancy_id: UUID, user: CandidateUser, session: Session) -> ApplicationOut:
    return await service.apply_to_vacancy(session, user, vacancy_id)
