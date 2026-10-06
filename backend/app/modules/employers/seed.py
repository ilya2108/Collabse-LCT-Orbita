"""Сид демо-данных работодателя: аккаунт жюри, корпоративный и личный профили компании,
участники (включая приглашённого без входа HR с фиксированным демо-токеном) и две
потребности (be junior / be middle) с подграфами.

Идемпотентен: upsert по натуральным ключам (`keycloak_sub`, `(company_id, title)`,
`email_hash` участника). Демо-пользователи создаются с `keycloak_sub = "seed:demo-…"`
(паттерн CLAUDE.md): при первом реальном входе с подтверждённым email строка
«усыновляется» Keycloak-пользователем. ПДн шифруются боевым кодом (`core/pii`).

Демо-токен приглашения HR — публичная фикстура сценария демо, не секрет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import get_pii_crypto
from app.models.employer import Company, CompanyMember, EmployerNeed, NeedVersion
from app.models.user import AppUser

DEMO_SUB_PREFIX = "seed:demo-"
DEMO_COMPANY_NAME = "ФСП.Орбита Демо"
DEMO_PERSONAL_COMPANY_NAME = "Личный профиль — демо-наниматель"
# фиксированный одноразовый токен приглашённого HR — для сценария демо и телеграм-бота
DEMO_HR_INVITE_TOKEN = "demo-orbita-hr-invite-2026"
JUDGE_EMPLOYER_EMAIL = "judge-employer@fsp.local"
PERSONAL_EMPLOYER_EMAIL = "demo-employer-personal@fsp.local"
DEMO_HR_EMAIL = "demo-hr@fsp.local"

# подграфы демо-потребностей (формат `need_version.subgraph`, PLAN.md §5.1)
NEED_JUNIOR_TITLE = "Backend-разработчик (junior)"
NEED_MIDDLE_TITLE = "Backend-разработчик (middle)"
NEED_SUBGRAPHS: dict[str, dict[str, Any]] = {
    NEED_JUNIOR_TITLE: {
        "nodes": [
            {"id": "be.B1", "min_level": 1, "importance": "must"},
            {"id": "be.B2", "min_level": 1, "importance": "must"},
            {"id": "be.B3", "min_level": 1, "importance": "should"},
            {"id": "be.D1", "min_level": 1, "importance": "must"},
        ]
    },
    NEED_MIDDLE_TITLE: {
        "nodes": [
            {"id": "be.B1", "min_level": 2, "importance": "must"},
            {"id": "be.B2", "min_level": 2, "importance": "must"},
            {"id": "be.B4", "min_level": 2, "importance": "must"},
            {"id": "be.D1", "min_level": 2, "importance": "must"},
            {"id": "be.D4", "min_level": 2, "importance": "should"},
            {"id": "be.C1", "min_level": 1, "importance": "nice"},
        ]
    },
}


async def ensure_user(
    session: AsyncSession,
    *,
    suffix: str,
    email: str,
    username: str,
    roles: list[str],
) -> tuple[AppUser, bool]:
    """Демо-пользователь по `keycloak_sub = seed:demo-<suffix>` (создаётся с согласиями)."""
    sub = f"{DEMO_SUB_PREFIX}{suffix}"
    user = (
        await session.execute(select(AppUser).where(AppUser.keycloak_sub == sub))
    ).scalar_one_or_none()
    if user is not None:
        return user, False
    now = datetime.now(UTC)
    user = AppUser(
        keycloak_sub=sub,
        username=username,
        email=email,
        email_hash=get_pii_crypto().email_hmac(email),
        email_verified=True,
        roles=roles,
        active_role=roles[0] if roles else None,
        consent_pdn_at=now,
        # = POLICY_VERSION фронта (content/privacy-policy.ru.tsx): иначе демо-учётки
        # при первом входе упираются в повторное согласие
        consent_policy_version="2026-10-06",
        consent_publish_at=now,
    )
    session.add(user)
    await session.flush()
    return user, True


async def ensure_company(
    session: AsyncSession,
    *,
    owner: AppUser,
    name: str,
    kind: str,
    description: str | None = None,
    contact_email: str | None = None,
) -> tuple[Company, bool]:
    company = (
        await session.execute(select(Company).where(Company.user_id == owner.id))
    ).scalar_one_or_none()
    if company is not None:
        return company, False
    crypto = get_pii_crypto()
    company = Company(
        user_id=owner.id,
        name=name,
        kind=kind,
        description=description,
        contact_email=contact_email,
        contact_email_hash=crypto.blind_index("contact_email", contact_email)
        if contact_email
        else None,
    )
    session.add(company)
    await session.flush()
    session.add(
        CompanyMember(
            company_id=company.id,
            user_id=owner.id,
            role="owner",
            status="active",
            accepted_at=datetime.now(UTC),
        )
    )
    return company, True


async def ensure_invited_hr(session: AsyncSession, company: Company, invited_by: AppUser) -> bool:
    """Приглашённый БЕЗ входа на платформу HR: `user_id IS NULL`, токен фиксированный."""
    crypto = get_pii_crypto()
    email_hash = crypto.blind_index("email", DEMO_HR_EMAIL)
    existing = (
        await session.execute(
            select(CompanyMember).where(
                CompanyMember.company_id == company.id, CompanyMember.email_hash == email_hash
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(
        CompanyMember(
            company_id=company.id,
            user_id=None,
            role="hr",
            status="invited",
            full_name="Орбитина Анна",
            full_name_hash=crypto.blind_index("full_name", "Орбитина Анна"),
            email=DEMO_HR_EMAIL,
            email_hash=email_hash,
            invite_channel="email",
            invite_token_hash=crypto.hmac_hex(DEMO_HR_INVITE_TOKEN),
            invited_by=invited_by.id,
            invited_at=datetime.now(UTC),
        )
    )
    return True


async def ensure_need(
    session: AsyncSession,
    *,
    company: Company,
    title: str,
    grade: str,
    salary_from: int,
    salary_to: int,
    first_months_tasks: str,
    stack_tags: list[str],
) -> tuple[EmployerNeed, bool]:
    need = (
        await session.execute(
            select(EmployerNeed).where(
                EmployerNeed.company_id == company.id, EmployerNeed.title == title
            )
        )
    ).scalar_one_or_none()
    if need is not None:
        return need, False
    need = EmployerNeed(
        company_id=company.id,
        title=title,
        first_months_tasks=first_months_tasks,
        specialization_code="be",
        grade=grade,
        stack_tags=stack_tags,
        salary_from=salary_from,
        salary_to=salary_to,
        format="remote",
        status="active",
    )
    session.add(need)
    await session.flush()
    session.add(
        NeedVersion(
            need_id=need.id,
            version=1,
            mode="verified",
            subgraph=NEED_SUBGRAPHS[title],
            filters={},
            result=[],
        )
    )
    return need, True


async def seed(session: AsyncSession) -> dict[str, int]:
    counters = {"users": 0, "companies": 0, "members": 0, "needs": 0}

    judge, created = await ensure_user(
        session,
        suffix="judge-employer",
        email=JUDGE_EMPLOYER_EMAIL,
        username="judge-employer",
        roles=["employer"],
    )
    counters["users"] += int(created)
    personal_owner, created = await ensure_user(
        session,
        suffix="personal-employer",
        email=PERSONAL_EMPLOYER_EMAIL,
        username="demo-personal-employer",
        roles=["employer"],
    )
    counters["users"] += int(created)

    corporate, created = await ensure_company(
        session,
        owner=judge,
        name=DEMO_COMPANY_NAME,
        kind="corporate",
        description="Демо-компания «ФСП.Орбита»: корпоративный профиль для сценария жюри",
        contact_email="hiring@fsp-orbita.local",
    )
    counters["companies"] += int(created)
    counters["members"] += int(created)  # owner-строка создаётся вместе с компанией
    _, created = await ensure_company(
        session,
        owner=personal_owner,
        name=DEMO_PERSONAL_COMPANY_NAME,
        kind="personal",
        description="Личный профиль нанимающего менеджера (демо)",
    )
    counters["companies"] += int(created)
    counters["members"] += int(created)

    counters["members"] += int(await ensure_invited_hr(session, corporate, judge))

    _, created = await ensure_need(
        session,
        company=corporate,
        title=NEED_JUNIOR_TITLE,
        grade="junior",
        salary_from=80_000,
        salary_to=130_000,
        first_months_tasks=(
            "Первые 3 месяца: закрыть долг по интеграционным тестам API заявок, "
            "перевести два отчёта на фоновые задачи, навести порядок в миграциях"
        ),
        stack_tags=["python", "fastapi", "postgresql"],
    )
    counters["needs"] += int(created)
    _, created = await ensure_need(
        session,
        company=corporate,
        title=NEED_MIDDLE_TITLE,
        grade="middle",
        salary_from=180_000,
        salary_to=260_000,
        first_months_tasks=(
            "Первые 3 месяца: спроектировать и вывести в прод сервис уведомлений, "
            "настроить наблюдаемость (метрики и алерты), разогнать медленные запросы отчётов"
        ),
        stack_tags=["python", "fastapi", "postgresql", "docker"],
    )
    counters["needs"] += int(created)

    return counters
