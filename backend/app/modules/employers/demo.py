"""Пересев демо-данных (`POST /internal/demo/reset`, X-Internal-Token).

Удаляет всё, что создают демо-сиды (`app_user` с `keycloak_sub LIKE 'seed:demo-%'` —
каскады сносят профили, подтверждения, компании, потребности, воронку и приглашения),
плюс инструмент проверки жюри (`pair_run`, `imported_candidate`) и `unverified`-версии
(PLAN.md §7.5), затем запускает демо-сиды заново. Строки `seed:` без префикса `demo-`
(чужие сиды) не трогаются.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employer import NeedVersion
from app.models.review import ImportedCandidate, PairRun
from app.models.user import AppUser
from app.modules.assessment.seed import seed as integrity_seed
from app.modules.employers.seed import DEMO_SUB_PREFIX
from app.modules.employers.seed import seed as employers_seed
from app.modules.matching.seed import seed as matching_seed


async def reset_demo(session: AsyncSession) -> dict[str, Any]:
    """Удаление демо-данных и повторный сид; возвращает счётчики для ответа API."""
    demo_users = (
        (
            await session.execute(
                select(AppUser.id).where(AppUser.keycloak_sub.like(f"{DEMO_SUB_PREFIX}%"))
            )
        )
        .scalars()
        .all()
    )
    deleted_users = 0
    if demo_users:
        result = await session.execute(delete(AppUser).where(AppUser.id.in_(demo_users)))
        deleted_users = result.rowcount or 0
    await session.execute(delete(ImportedCandidate))
    await session.execute(delete(PairRun))
    await session.execute(delete(NeedVersion).where(NeedVersion.mode == "unverified"))
    await session.commit()

    employers_report = await employers_seed(session)
    await session.commit()
    matching_report = await matching_seed(session)
    await session.commit()
    # демо-сигналы целостности: завершённые сессии с телеметрией для бейджа в выдаче
    integrity_report = await integrity_seed(session)
    await session.commit()
    return {
        "status": "ok",
        "deleted_demo_users": deleted_users,
        "employers": employers_report,
        "matching": matching_report,
        "integrity": integrity_report,
    }
