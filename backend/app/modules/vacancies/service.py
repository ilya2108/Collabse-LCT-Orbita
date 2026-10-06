"""Сервис модуля `vacancies`: публичный список опубликованных вакансий, идемпотентный
отклик кандидата, вакансии своей компании со счётчиком откликов.

Ключевые решения:
- наружу отдаются только `status = published`; ПДн в ответах нет (название компании —
  публичное поле);
- `need_nodes_count` — число узлов последней версии подграфа связанной потребности:
  кандидату видно, что отклик будет оценён по конкретным узлам графа;
- повторный отклик не ошибка: возвращается уже существующий `Application`
  (уникальность пары (candidate_id, vacancy_id) гарантирует БД);
- членство в компании для `/vacancies/mine` определяется тем же путём, что в module
  employers (`get_or_create_company`), — вакансии чужих компаний не видны.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.employer import Application, Company, NeedVersion, Vacancy
from app.models.user import AppUser
from app.modules.candidates.service import find_profile, get_or_create_profile
from app.modules.vacancies.schemas import (
    ApplicationOut,
    VacancyMineOut,
    VacancyPublicOut,
)

# описание в карточке списка — короткий фрагмент, полный текст в списке не нужен
DESCRIPTION_PREVIEW_LIMIT = 280


def shorten_description(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= DESCRIPTION_PREVIEW_LIMIT:
        return text or None
    return text[: DESCRIPTION_PREVIEW_LIMIT - 1].rstrip() + "…"


def subgraph_nodes_count(version: NeedVersion | None) -> int | None:
    if version is None:
        return None
    nodes = version.subgraph.get("nodes") if isinstance(version.subgraph, dict) else None
    return len(nodes) if isinstance(nodes, list) else None


async def latest_versions_by_need(
    session: AsyncSession, need_ids: list[uuid.UUID]
) -> dict[uuid.UUID, NeedVersion]:
    """Последняя версия подграфа по каждой потребности (для `need_nodes_count`)."""
    if not need_ids:
        return {}
    versions = (
        (
            await session.execute(
                select(NeedVersion)
                .where(NeedVersion.need_id.in_(need_ids))
                .order_by(NeedVersion.need_id, NeedVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[uuid.UUID, NeedVersion] = {}
    for version in versions:
        latest.setdefault(version.need_id, version)
    return latest


async def applied_vacancy_ids(session: AsyncSession, user: AppUser) -> set[uuid.UUID]:
    """Вакансии, на которые текущий пользователь уже откликался (пусто без профиля)."""
    profile = await find_profile(session, user.id)
    if profile is None:
        return set()
    ids = (
        (
            await session.execute(
                select(Application.vacancy_id).where(Application.candidate_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    return set(ids)


async def list_published(session: AsyncSession, user: AppUser) -> list[VacancyPublicOut]:
    """Публичный список опубликованных вакансий (кандидату — с флагом `applied`)."""
    rows = (
        await session.execute(
            select(Vacancy, Company.name)
            .join(Company, Company.id == Vacancy.company_id)
            .where(Vacancy.status == "published")
            .order_by(Vacancy.published_at.desc().nulls_last(), Vacancy.created_at.desc())
        )
    ).all()
    need_ids = [v.need_id for v, _ in rows if v.need_id is not None]
    versions = await latest_versions_by_need(session, need_ids)
    applied = await applied_vacancy_ids(session, user)
    return [
        VacancyPublicOut(
            id=vacancy.id,
            title=vacancy.title,
            company_name=company_name,
            specialization_code=vacancy.specialization_code,
            grade=vacancy.grade,
            salary_from=vacancy.salary_from,
            salary_to=vacancy.salary_to,
            format=vacancy.format,
            description=shorten_description(vacancy.description),
            need_nodes_count=subgraph_nodes_count(
                versions.get(vacancy.need_id) if vacancy.need_id else None
            ),
            applied=vacancy.id in applied,
        )
        for vacancy, company_name in rows
    ]


async def apply_to_vacancy(
    session: AsyncSession, user: AppUser, vacancy_id: uuid.UUID
) -> ApplicationOut:
    """Отклик кандидата: 404 на неопубликованную, повтор возвращает тот же отклик."""
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None or vacancy.status != "published":
        raise not_found("Вакансия не найдена или не опубликована")
    profile = await get_or_create_profile(session, user)
    existing = (
        await session.execute(
            select(Application).where(
                Application.candidate_id == profile.id,
                Application.vacancy_id == vacancy.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return build_application(existing)
    application = Application(
        candidate_id=profile.id,
        vacancy_id=vacancy.id,
        status="sent",
        sent_at=datetime.now(UTC),
    )
    session.add(application)
    await session.flush()
    await session.commit()
    return build_application(application)


def build_application(application: Application) -> ApplicationOut:
    return ApplicationOut(
        id=application.id,
        vacancy_id=application.vacancy_id,
        status=application.status,
        sent_at=application.sent_at,
    )


async def list_company_vacancies(session: AsyncSession, company: Company) -> list[VacancyMineOut]:
    """Вакансии компании (любой статус) + счётчик откликов на каждую."""
    vacancies = (
        (
            await session.execute(
                select(Vacancy)
                .where(Vacancy.company_id == company.id)
                .order_by(Vacancy.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    counts: dict[uuid.UUID, int] = {}
    if vacancies:
        count_rows = (
            await session.execute(
                select(Application.vacancy_id, func.count())
                .where(Application.vacancy_id.in_([v.id for v in vacancies]))
                .group_by(Application.vacancy_id)
            )
        ).all()
        counts = dict(count_rows)
    versions = await latest_versions_by_need(
        session, [v.need_id for v in vacancies if v.need_id is not None]
    )
    return [
        VacancyMineOut(
            id=vacancy.id,
            title=vacancy.title,
            specialization_code=vacancy.specialization_code,
            grade=vacancy.grade,
            salary_from=vacancy.salary_from,
            salary_to=vacancy.salary_to,
            format=vacancy.format,
            status=vacancy.status,
            description=shorten_description(vacancy.description),
            need_nodes_count=subgraph_nodes_count(
                versions.get(vacancy.need_id) if vacancy.need_id else None
            ),
            applications_count=counts.get(vacancy.id, 0),
            published_at=vacancy.published_at,
        )
        for vacancy in vacancies
    ]
