"""Роутер модуля `candidates` (PLAN.md §7.5). День 6: `GET|PUT /candidates/me`.

Доступ — только роли `candidate` (и `admin`); запись ПДн — только при согласии
на обработку ПДн (`require_consent` → 403). Чтение создаёт пустой профиль с псевдонимом,
ПДн при этом не пишутся, поэтому согласие для GET не требуется.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ApiError, not_found
from app.core.security import ROLE_ADMIN, ROLE_CANDIDATE, require_consent, require_roles
from app.models.candidate import CandidateCategory, CandidateSkillState
from app.models.user import AppUser
from app.modules.assessment.sessions import api_node_state, skills_catalog
from app.modules.candidates import service, summary
from app.modules.candidates.schemas import (
    CandidateMeIn,
    CandidateMeOut,
    CategoryOut,
    GraphSummaryOut,
    SkillStateOut,
    SkillStatesOut,
)

router = APIRouter(prefix="/candidates", tags=["candidates"])

CandidateUser = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_ADMIN))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/me", response_model=CandidateMeOut, summary="Мой профиль кандидата")
async def get_me(user: CandidateUser, session: Session) -> CandidateMeOut:
    profile = await service.get_or_create_profile(session, user)
    return service.build_me(profile)


@router.get(
    "/me/category",
    response_model=CategoryOut,
    summary="Моя категория (специализация × подтверждённый грейд)",
    responses={404: {"description": "category_not_assigned — категория ещё не присвоена"}},
)
async def get_my_category(user: CandidateUser, session: Session) -> CategoryOut:
    """Категория по специализации профиля; если специализация не выбрана — последняя
    присвоенная. Порядок запросов: профиль → категория (важно для тестов)."""
    profile = await service.find_profile(session, user.id)
    not_assigned = ApiError(
        404,
        "category_not_assigned",
        "Категория ещё не присвоена: заполните профиль и пройдите тест на грейд",
    )
    if profile is None:
        raise not_assigned
    query = select(CandidateCategory).where(CandidateCategory.candidate_id == profile.id)
    if profile.specialization_code:
        query = query.where(CandidateCategory.specialization_code == profile.specialization_code)
    query = query.order_by(desc(CandidateCategory.computed_at)).limit(1)
    category = (await session.execute(query)).scalar_one_or_none()
    if category is None:
        raise not_assigned
    return CategoryOut(
        specialization=category.specialization_code,
        grade=category.grade,
        grade_confirmed=True,
        q_level=category.q_level,
        computed_at=category.computed_at,
    )


@router.get(
    "/me/skill-states",
    response_model=SkillStatesOut,
    summary="Мои узлы графа навыков: персональный слой поверх общего графа",
    responses={404: {"description": "not_found — профиль кандидата ещё не создан"}},
)
async def get_my_skill_states(user: CandidateUser, session: Session) -> SkillStatesOut:
    """Лёгкий слой для подсветки «моего» графа: по каждому узлу кандидата — состояние
    fresh | fading | expired | off (пороги fresh/dim — из graph.yaml специализации узла,
    активность — active_level ≥ 1). Строк состояния нет — пустой список; ПДн не отдаются."""
    profile = await service.find_profile(session, user.id)
    if profile is None:
        raise not_found("Профиль кандидата ещё не создан")
    rows = (
        (
            await session.execute(
                select(CandidateSkillState)
                .where(CandidateSkillState.candidate_id == profile.id)
                .order_by(CandidateSkillState.node_id)
            )
        )
        .scalars()
        .all()
    )
    catalog = skills_catalog()
    states: list[SkillStateOut] = []
    for row in rows:
        spec_code = row.node_id.split(".", 1)[0]
        spec = catalog.spec(spec_code)
        if spec is None:
            state = "off"  # специализация не в справочнике — узел на графе не подсвечиваем
        else:
            thresholds = spec.file.thresholds
            state = api_node_state(
                row.active_level >= 1, float(row.w_now), thresholds.fresh, thresholds.dim
            )
        states.append(
            SkillStateOut(
                node_id=row.node_id,
                specialization_code=spec_code,
                active_level=row.active_level,
                w_now=float(row.w_now),
                state=state,
            )
        )
    return SkillStatesOut(states=states)


@router.get(
    "/me/graph-summary",
    response_model=GraphSummaryOut,
    summary="Разбор моего графа навыков человеческим языком (LLM с rules-фолбэком)",
    responses={404: {"description": "not_found — профиль или снимки узлов ещё не созданы"}},
)
async def get_my_graph_summary(user: CandidateUser, session: Session) -> GraphSummaryOut:
    """Три абзаца по-русски: сильная зона, что затухает, что подтянуть до следующего грейда.
    В LLM уходят только обезличенные данные (коды узлов, состояния, баллы); при любой
    ошибке LLM — детерминированный rules-фолбэк. Ответ кэшируется на час."""
    profile = await service.find_profile(session, user.id)
    if profile is None:
        raise not_found("Профиль кандидата ещё не создан")
    return await summary.build_graph_summary(session, profile)


@router.put(
    "/me",
    response_model=CandidateMeOut,
    summary="Обновить профиль (псевдоним, город, отрасль, стек, ПДн — при согласии)",
    responses={403: {"description": "forbidden — нет согласия на обработку ПДн"}},
)
async def update_me(
    payload: CandidateMeIn, user: CandidateUser, session: Session
) -> CandidateMeOut:
    require_consent(user)
    profile = await service.get_or_create_profile(session, user)
    profile = await service.update_profile(session, profile, payload.changes())
    return service.build_me(profile)
