"""Роутер модуля `tma` — HTTP-интерфейс Telegram Mini App (пути `/api/v1/tma/*`).

Авторизация — заголовок `X-Telegram-Init-Data` (`deps.get_tma_actor`), а не JWT.
Все ручки — тонкие обёртки: кандидатские вызывают те же обработчики, что веб
(`GET /candidates/me/invitations`, accept/decline), работодательские — сервис
`employers` и обработчики `matching` (воронка собирается тем же кодом, что в вебе,
без дублирования). Правил здесь нет — только проверка вида актора.

Для «HR через Telegram» (участник без аккаунта) действующим лицом аудита выступает
владелец компании — ровно как в callback-обработчиках бота
(`tgbot/handlers._employer_context`); сам HR в `app_user` отсутствует.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ApiError, forbidden, not_found
from app.models.candidate import CandidateCategory
from app.models.employer import Company
from app.models.user import AppUser
from app.modules.employers import service as employers_service
from app.modules.matching import router as matching_api
from app.modules.matching import service as matching_service
from app.modules.matching.schemas import (
    InvitationAcceptOut,
    InvitationDeclineIn,
    InvitationOut,
    InvitationsOut,
    PipelineItemOut,
    PipelineOut,
    PipelineStageIn,
)
from app.modules.tma.deps import TmaActor, get_tma_actor
from app.modules.tma.schemas import TmaDeclineIn, TmaMeOut, TmaNeedsOut

router = APIRouter(prefix="/tma", tags=["tma"])

Actor = Annotated[TmaActor, Depends(get_tma_actor)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _candidate_user(actor: TmaActor) -> AppUser:
    if actor.kind != "candidate" or actor.user is None:
        raise forbidden("Ручка доступна только кандидату")
    return actor.user


async def _company_context(session: AsyncSession, actor: TmaActor) -> tuple[Company, str]:
    """Компания актора-работодателя и его роль в ней (employer | member)."""
    if actor.kind == "employer" and actor.user is not None:
        return await employers_service.get_or_create_company(session, actor.user)
    if actor.kind == "member" and actor.member is not None:
        company = await session.get(Company, actor.member.company_id)
        if company is None:
            raise not_found("Компания не найдена")
        return company, actor.member.role
    raise forbidden("Ручка доступна только работодателю")


async def _acting_employer(session: AsyncSession, actor: TmaActor) -> AppUser:
    """Пользователь, от имени которого выполняются действия работодателя.

    Участник без аккаунта действует от имени владельца компании (как в обработчиках
    бота) — компания у владельца та же, поэтому правило «только потребности своей
    компании» сохраняется автоматически.
    """
    if actor.kind == "employer" and actor.user is not None:
        return actor.user
    if actor.kind != "member" or actor.member is None:
        raise forbidden("Ручка доступна только работодателю")
    if actor.member.user_id is not None:
        user = await session.get(AppUser, actor.member.user_id)
        if user is not None:
            return user
    company = await session.get(Company, actor.member.company_id)
    owner = await session.get(AppUser, company.user_id) if company is not None else None
    if owner is None:
        raise not_found("Компания не найдена")
    return owner


# --- общие ------------------------------------------------------------------------------


@router.get("/me", response_model=TmaMeOut, summary="Кто я в мини-аппе")
async def get_me(actor: Actor, session: Session) -> TmaMeOut:
    if actor.kind == "candidate" and actor.user is not None:
        try:
            profile = await matching_service.get_candidate_profile(session, actor.user)
        except ApiError:
            # профиль ещё не создан на платформе — мини-апп покажет подсказку
            return TmaMeOut(kind="candidate")
        category = (
            await session.execute(
                select(CandidateCategory).where(CandidateCategory.candidate_id == profile.id)
            )
        ).scalar_one_or_none()
        return TmaMeOut(
            kind="candidate",
            pseudonym=profile.pseudonym,
            grade=category.grade if category is not None else None,
        )
    company, role = await _company_context(session, actor)
    return TmaMeOut(kind=actor.kind, company_name=company.name, role=role)


# --- кандидат: приглашения ---------------------------------------------------------------


@router.get(
    "/invitations",
    response_model=InvitationsOut,
    summary="Мои входящие приглашения (как GET /candidates/me/invitations)",
)
async def my_invitations(actor: Actor, session: Session) -> InvitationsOut:
    return await matching_api.candidate_invitations(_candidate_user(actor), session)


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=InvitationAcceptOut,
    summary="Принять приглашение (контракт веб-принятия, возвращает контакты компании)",
    responses={409: {"description": "invitation_closed | invitation_expired"}},
)
async def accept_invitation(
    invitation_id: UUID, actor: Actor, session: Session, request: Request
) -> InvitationAcceptOut:
    return await matching_api.accept_invitation(
        invitation_id, _candidate_user(actor), session, request
    )


@router.post(
    "/invitations/{invitation_id}/decline",
    response_model=InvitationOut,
    summary="Отклонить приглашение (причина — по желанию)",
)
async def decline_invitation(
    invitation_id: UUID,
    actor: Actor,
    session: Session,
    request: Request,
    payload: TmaDeclineIn | None = None,
) -> InvitationOut:
    body = InvitationDeclineIn(reason_code=payload.reason_code if payload else None, text=None)
    return await matching_api.decline_invitation(
        invitation_id, body, _candidate_user(actor), session, request
    )


# --- работодатель и HR: потребности и воронка --------------------------------------------


@router.get(
    "/needs",
    response_model=TmaNeedsOut,
    summary="Активные потребности компании актора (employer | member)",
)
async def my_needs(actor: Actor, session: Session) -> TmaNeedsOut:
    company, _ = await _company_context(session, actor)
    needs = [
        need
        for need in await employers_service.list_needs(session, company.id)
        if need.status == "active"
    ]
    return TmaNeedsOut(
        items=[employers_service.build_need(need) for need in needs], total=len(needs)
    )


@router.get(
    "/needs/{need_id}/pipeline",
    response_model=PipelineOut,
    summary="Воронка потребности — колонки как в вебе (псевдонимы, contacts после accepted)",
)
async def need_pipeline(need_id: UUID, actor: Actor, session: Session) -> PipelineOut:
    # сборка колонок не дублируется: вызывается веб-обработчик от имени действующего
    # пользователя (его компания совпадает с компанией актора)
    return await matching_api.get_pipeline(need_id, await _acting_employer(session, actor), session)


@router.patch(
    "/needs/{need_id}/pipeline/{candidate_id}",
    response_model=PipelineItemOut,
    summary="Перевести стадию кандидата (те же правила переходов, что в вебе)",
    responses={409: {"description": "pipeline_transition_invalid"}},
)
async def set_pipeline_stage(
    need_id: UUID,
    candidate_id: UUID,
    payload: PipelineStageIn,
    actor: Actor,
    session: Session,
    request: Request,
) -> PipelineItemOut:
    return await matching_api.patch_pipeline_stage(
        need_id, candidate_id, payload, await _acting_employer(session, actor), session, request
    )
