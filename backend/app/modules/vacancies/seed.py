"""Сид демо-вакансий: 4 опубликованные вакансии двух сид-компаний (`employers`-сид)
и несколько откликов сид-кандидатов (`matching`-сид) — чтобы счётчики не были нулевыми.

Идемпотентен: вакансия — upsert по `(company_id, title)`, отклик — по
`(candidate_id, vacancy_id)`. Запускается после сидов employers и matching:

    .venv/bin/python -m app.modules.vacancies.seed

Компании находятся по названиям из `app.modules.employers.seed`; две backend-вакансии
линкуются к существующим потребностям с совпадающей специализацией (be).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import dispose_engine, get_session_factory
from app.models.candidate import CandidateProfile
from app.models.employer import Application, Company, EmployerNeed, Vacancy
from app.models.user import AppUser
from app.modules.employers.seed import (
    DEMO_COMPANY_NAME,
    DEMO_PERSONAL_COMPANY_NAME,
    NEED_JUNIOR_TITLE,
    NEED_MIDDLE_TITLE,
)

# (company_name, need_title | None, параметры вакансии)
VACANCIES: list[tuple[str, str | None, dict[str, Any]]] = [
    (
        DEMO_COMPANY_NAME,
        NEED_JUNIOR_TITLE,
        {
            "title": "Backend-разработчик (Junior)",
            "description": (
                "Ищем джуниора в команду API: FastAPI, PostgreSQL, тесты. "
                "Наставник, код-ревью, понятный план первых трёх месяцев."
            ),
            "specialization_code": "be",
            "grade": "junior",
            "salary_from": 90_000,
            "salary_to": 140_000,
            "format": "remote",
        },
    ),
    (
        DEMO_COMPANY_NAME,
        NEED_MIDDLE_TITLE,
        {
            "title": "Backend-разработчик в команду платформы",
            "description": (
                "Сервис уведомлений, наблюдаемость и ускорение медленных запросов. "
                "Python 3.12, FastAPI, PostgreSQL, Docker; прод с первого спринта."
            ),
            "specialization_code": "be",
            "grade": "middle",
            "salary_from": 150_000,
            "salary_to": 220_000,
            "format": "hybrid",
        },
    ),
    (
        DEMO_COMPANY_NAME,
        None,
        {
            "title": "Фронтенд-разработчик в команду платформы",
            "description": (
                "React 18 + TypeScript + Vite: кабинеты кандидата и работодателя, "
                "граф навыков на ECharts. Дизайн-система готова, вёрстка — по токенам."
            ),
            "specialization_code": "fe",
            "grade": "middle",
            "salary_from": 160_000,
            "salary_to": 230_000,
            "format": "remote",
        },
    ),
    (
        DEMO_PERSONAL_COMPANY_NAME,
        None,
        {
            "title": "Аналитик данных в продуктовую команду",
            "description": (
                "Витрины продуктовых метрик, A/B-эксперименты и отчёты для команды. "
                "SQL, Python, базовая статистика; менторство от нанимающего менеджера."
            ),
            "specialization_code": "data",
            "grade": "junior",
            "salary_from": 95_000,
            "salary_to": 150_000,
            "format": "hybrid",
        },
    ),
]

# отклики сид-кандидатов (suffix из matching-сида) на вакансии по title
APPLICATIONS: list[tuple[str, str]] = [
    ("demo-cand-13", "Backend-разработчик в команду платформы"),
    ("demo-cand-14", "Backend-разработчик в команду платформы"),
    ("demo-cand-01", "Backend-разработчик (Junior)"),
]


async def find_company(session: AsyncSession, name: str) -> Company | None:
    return (await session.execute(select(Company).where(Company.name == name))).scalar_one_or_none()


async def find_need(session: AsyncSession, company_id: Any, title: str) -> EmployerNeed | None:
    return (
        await session.execute(
            select(EmployerNeed).where(
                EmployerNeed.company_id == company_id, EmployerNeed.title == title
            )
        )
    ).scalar_one_or_none()


async def find_demo_profile(session: AsyncSession, username: str) -> CandidateProfile | None:
    """Профиль сид-кандидата по `keycloak_sub = seed:<username>` (паттерн matching-сида)."""
    return (
        await session.execute(
            select(CandidateProfile)
            .join(AppUser, AppUser.id == CandidateProfile.user_id)
            .where(AppUser.keycloak_sub == f"seed:{username}")
        )
    ).scalar_one_or_none()


async def ensure_vacancy(
    session: AsyncSession, company: Company, need: EmployerNeed | None, fields: dict[str, Any]
) -> tuple[Vacancy, bool]:
    vacancy = (
        await session.execute(
            select(Vacancy).where(
                Vacancy.company_id == company.id, Vacancy.title == fields["title"]
            )
        )
    ).scalar_one_or_none()
    if vacancy is not None:
        return vacancy, False
    vacancy = Vacancy(
        company_id=company.id,
        need_id=need.id if need is not None else None,
        status="published",
        published_at=datetime.now(UTC),
        **fields,
    )
    session.add(vacancy)
    await session.flush()
    return vacancy, True


async def ensure_application(
    session: AsyncSession, profile: CandidateProfile, vacancy: Vacancy
) -> bool:
    existing = (
        await session.execute(
            select(Application).where(
                Application.candidate_id == profile.id, Application.vacancy_id == vacancy.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(Application(candidate_id=profile.id, vacancy_id=vacancy.id, status="sent"))
    await session.flush()
    return True


async def seed(session: AsyncSession) -> dict[str, int]:
    counters = {"vacancies": 0, "applications": 0}

    created_by_title: dict[str, Vacancy] = {}
    for company_name, need_title, fields in VACANCIES:
        company = await find_company(session, company_name)
        if company is None:
            continue  # employers-сид не отработал — компанию не создаём за него
        need = await find_need(session, company.id, need_title) if need_title else None
        vacancy, created = await ensure_vacancy(session, company, need, fields)
        created_by_title[fields["title"]] = vacancy
        counters["vacancies"] += int(created)

    for username, vacancy_title in APPLICATIONS:
        vacancy = created_by_title.get(vacancy_title)
        if vacancy is None:
            continue
        profile = await find_demo_profile(session, username)
        if profile is None:
            continue  # matching-сид не отработал — откликаться некому
        counters["applications"] += int(await ensure_application(session, profile, vacancy))

    return counters


async def main() -> int:
    factory = get_session_factory()
    try:
        async with factory() as session:
            counters = await seed(session)
            await session.commit()
    finally:
        await dispose_engine()
    summary = ", ".join(f"{key}={value}" for key, value in counters.items())
    print(f"seed: vacancies: {summary}")  # noqa: T201 — CLI-вывод сида
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
