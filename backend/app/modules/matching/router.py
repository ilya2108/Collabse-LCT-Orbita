"""Роутер модуля `matching` (PLAN.md §5.3–§5.6): ранжированная выдача по потребности,
мини-CRM (pipeline) и приглашения.

Префикса у роутера нет: пути `/needs/*`, `/invitations/*`, `/employers/me/invitations`
и `/candidates/me/invitations` объявляются полностью. Сторона работодателя — роль
`employer`, сторона кандидата — `candidate` (плюс `admin`). Контакты кандидата отдаёт
только `GET /invitations/{id}/contacts` при наличии `contact_grant` (после принятия).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip
from app.core.db import get_session
from app.core.errors import not_found
from app.core.pagination import ListParams, list_params
from app.core.security import ROLE_ADMIN, ROLE_CANDIDATE, ROLE_EMPLOYER, require_roles
from app.models.candidate import CandidateProfile
from app.models.employer import Invitation
from app.models.user import AppUser
from app.modules.employers import service as employers_service
from app.modules.matching import service, summary
from app.modules.matching.schemas import (
    BankOut,
    CandidateContactsOut,
    CompanyContactsOut,
    InvitationAcceptOut,
    InvitationCreateIn,
    InvitationCreateOut,
    InvitationDeclineIn,
    InvitationOut,
    InvitationsOut,
    NeedCandidatesOut,
    PipelineAddIn,
    PipelineColumnOut,
    PipelineItemOut,
    PipelineOut,
    PipelineStageIn,
    SelectionSummaryOut,
)

router = APIRouter(tags=["matching"])

EmployerUser = Annotated[AppUser, Depends(require_roles(ROLE_EMPLOYER, ROLE_ADMIN))]
CandidateUser = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_ADMIN))]
Session = Annotated[AsyncSession, Depends(get_session)]


# --- подбор -----------------------------------------------------------------------------


@router.get(
    "/needs/{need_id}/candidates",
    response_model=NeedCandidatesOut,
    summary="Ранжированные кандидаты под потребность (с объяснением позиции)",
)
async def get_need_candidates(
    need_id: UUID, user: EmployerUser, session: Session
) -> NeedCandidatesOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    need = await employers_service.get_company_need(session, company.id, need_id)
    nodes, basis = await service.resolve_need_nodes(session, need)
    candidates = await service.collect_candidates(session, need, company.id, [n.id for n in nodes])
    items = service.rank_candidates(nodes, need.grade, candidates)
    if basis == "grade_profile":
        for item in items:
            item.explanation.basis = "grade_profile"
    return NeedCandidatesOut(need_id=need.id, basis=basis, items=items, total=len(items))


@router.get(
    "/employers/needs/{need_id}/selection-summary",
    response_model=SelectionSummaryOut,
    summary="Саммари картины по кандидатам на потребность (LLM с rules-фолбэком)",
    responses={404: {"description": "not_found — потребность чужая или подборка не сформирована"}},
)
async def get_selection_summary(
    need_id: UUID, user: EmployerUser, session: Session
) -> SelectionSummaryOut:
    """2–3 абзаца по-русски: хватает ли кандидатов, оверфит или недобор, какие must-узлы
    реально понизить до should. В LLM уходят только псевдонимы, коды узлов и покрытия;
    при любой ошибке LLM — детерминированный rules-фолбэк. Ответ кэшируется на час."""
    company, _ = await employers_service.get_or_create_company(session, user)
    need = await employers_service.get_company_need(session, company.id, need_id)
    return await summary.build_selection_summary(session, need, company.id)


# --- банк кандидатов ----------------------------------------------------------------------


@router.get(
    "/matching/bank",
    response_model=BankOut,
    summary="Банк кандидатов: все с категорией и опубликованным профилем (псевдонимы)",
)
async def get_candidate_bank(
    user: EmployerUser,
    session: Session,
    params: Annotated[ListParams, Depends(list_params)],
    specialization: Annotated[
        str | None, Query(description="Код специализации (be/fe/data)")
    ] = None,
    grade: Annotated[
        Literal["junior", "middle", "senior"] | None, Query(description="Грейд")
    ] = None,
) -> BankOut:
    """Витрина банка: кандидаты под псевдонимами, скрытые от компании не показываются."""
    company, _ = await employers_service.get_or_create_company(session, user)
    items = await service.list_bank_candidates(session, company.id, specialization, grade)
    page = items[params.offset : params.offset + params.limit]
    return BankOut(items=page, total=len(items), limit=params.limit, offset=params.offset)


# --- мини-CRM ---------------------------------------------------------------------------


@router.get(
    "/needs/{need_id}/pipeline",
    response_model=PipelineOut,
    summary="Воронка потребности (колонки стадий; кандидаты — псевдонимами)",
)
async def get_pipeline(need_id: UUID, user: EmployerUser, session: Session) -> PipelineOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    need = await employers_service.get_company_need(session, company.id, need_id)
    entries = await service.get_pipeline_entries(session, need.id)
    version = await service.latest_verified_version(session, need.id)
    scores: dict[str, float] = {}
    for row in (version.result if version else None) or []:
        if isinstance(row, dict) and row.get("candidate_id") is not None:
            scores[str(row["candidate_id"])] = row.get("score")
    profiles_by_id: dict[UUID, CandidateProfile] = {}
    invitation_statuses: dict[UUID, str] = {}
    integrity_levels: dict[UUID, str | None] = {}
    if entries:
        profiles = (
            (
                await session.execute(
                    select(CandidateProfile).where(
                        CandidateProfile.id.in_({e.candidate_id for e in entries})
                    )
                )
            )
            .scalars()
            .all()
        )
        profiles_by_id = {p.id: p for p in profiles}
        invitation_ids = {e.invitation_id for e in entries if e.invitation_id is not None}
        if invitation_ids:
            invitations = (
                (await session.execute(select(Invitation).where(Invitation.id.in_(invitation_ids))))
                .scalars()
                .all()
            )
            invitation_statuses = {i.id: i.status for i in invitations}
        # бейдж целостности: уровень последней завершённой сессии теста кандидата
        integrity_levels = await service.latest_integrity_by_candidate(
            session, [e.candidate_id for e in entries]
        )
    columns: dict[str, list[PipelineItemOut]] = {
        stage: [] for stage in service.PIPELINE_STAGE_ORDER
    }
    for entry in entries:
        item = service.build_pipeline_item(
            entry,
            profiles_by_id.get(entry.candidate_id),
            invitation_statuses.get(entry.invitation_id) if entry.invitation_id else None,
            scores.get(str(entry.candidate_id)),
            integrity_levels.get(entry.candidate_id),
        )
        columns[entry.stage].append(item)
    return PipelineOut(
        need_id=need.id,
        columns=[PipelineColumnOut(stage=stage, items=items) for stage, items in columns.items()],
        total=len(entries),
    )


@router.post(
    "/needs/{need_id}/pipeline",
    response_model=PipelineItemOut,
    status_code=201,
    summary="Добавить кандидата в шорт-лист потребности",
)
async def add_pipeline_entry(
    need_id: UUID,
    payload: PipelineAddIn,
    user: EmployerUser,
    session: Session,
    request: Request,
) -> PipelineItemOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    need = await employers_service.get_company_need(session, company.id, need_id)
    entry = await service.add_to_pipeline(
        session, need, user, payload.candidate_id, payload.note, ip=client_ip(request)
    )
    return service.build_pipeline_item(entry, None, None, None)


@router.patch(
    "/needs/{need_id}/pipeline/{candidate_id}",
    response_model=PipelineItemOut,
    summary="Перевести стадию кандидата в воронке",
    responses={409: {"description": "pipeline_transition_invalid"}},
)
async def patch_pipeline_stage(
    need_id: UUID,
    candidate_id: UUID,
    payload: PipelineStageIn,
    user: EmployerUser,
    session: Session,
    request: Request,
) -> PipelineItemOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    need = await employers_service.get_company_need(session, company.id, need_id)
    entry = await service.set_pipeline_stage(
        session, need, user, candidate_id, payload.stage, payload.comment, ip=client_ip(request)
    )
    return service.build_pipeline_item(entry, None, None, None)


# --- приглашения: сторона работодателя ---------------------------------------------------


@router.post(
    "/invitations",
    response_model=InvitationCreateOut,
    status_code=201,
    summary="Пригласить кандидата (зарплата «от–до» обязательна; лимиты §5.6)",
    responses={
        409: {"description": "invitation_already_open | invitation_quota | candidate_daily_limit"}
    },
)
async def create_invitation(
    payload: InvitationCreateIn, user: EmployerUser, session: Session, request: Request
) -> InvitationCreateOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    need = None
    if payload.need_id is not None:
        need = await employers_service.get_company_need(session, company.id, payload.need_id)
    invitation, below_floor = await service.create_invitation(
        session,
        company,
        user,
        candidate_id=payload.candidate_id,
        need=need,
        title=payload.title,
        message=payload.message,
        salary_from=payload.salary_from,
        salary_to=payload.salary_to,
        contact_method=payload.contact_method,
        ip=client_ip(request),
    )
    return InvitationCreateOut(
        invitation=service.build_invitation(invitation, contact_method_visible=True),
        below_salary_floor=below_floor,
    )


@router.get(
    "/employers/me/invitations",
    response_model=InvitationsOut,
    summary="Приглашения моей компании (кандидаты — псевдонимами)",
)
async def employer_invitations(user: EmployerUser, session: Session) -> InvitationsOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    invitations, pseudonyms = await service.list_company_invitations(session, company.id)
    return InvitationsOut(
        items=[
            service.build_invitation(
                i,
                candidate_pseudonym=pseudonyms.get(i.candidate_id),
                contact_method_visible=True,
            )
            for i in invitations
        ],
        total=len(invitations),
    )


@router.get(
    "/invitations/{invitation_id}/contacts",
    response_model=CandidateContactsOut,
    summary="Контакты кандидата (только после принятия приглашения; пишется аудит)",
    responses={403: {"description": "forbidden — нет contact_grant или согласия"}},
)
async def get_invitation_contacts(
    invitation_id: UUID, user: EmployerUser, session: Session, request: Request
) -> CandidateContactsOut:
    company, _ = await employers_service.get_or_create_company(session, user)
    profile, candidate_user = await service.reveal_candidate_contacts(
        session, company, user, invitation_id, ip=client_ip(request)
    )
    return CandidateContactsOut(
        pseudonym=profile.pseudonym,
        full_name=profile.full_name,
        email=candidate_user.email if candidate_user else None,
        phone=profile.phone,
        telegram=profile.telegram,
    )


# --- приглашения: сторона кандидата ------------------------------------------------------


@router.get(
    "/candidates/me/invitations",
    response_model=InvitationsOut,
    summary="Мои входящие приглашения (кандидат)",
)
async def candidate_invitations(user: CandidateUser, session: Session) -> InvitationsOut:
    profile = await service.get_candidate_profile(session, user)
    invitations, names = await service.list_candidate_invitations(session, profile)
    return InvitationsOut(
        items=[
            service.build_invitation(
                i,
                company_name=names.get(i.company_id),
                contact_method_visible=i.status == "accepted",
            )
            for i in invitations
        ],
        total=len(invitations),
    )


@router.post(
    "/invitations/{invitation_id}/view",
    response_model=InvitationOut,
    summary="Отметить приглашение просмотренным (sent → viewed)",
)
async def view_invitation(
    invitation_id: UUID, user: CandidateUser, session: Session
) -> InvitationOut:
    invitation = await service.view_invitation(session, user, invitation_id)
    return service.build_invitation(invitation)


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=InvitationAcceptOut,
    summary="Принять приглашение: контакты открываются обеим сторонам",
    responses={409: {"description": "invitation_closed | invitation_expired"}},
)
async def accept_invitation(
    invitation_id: UUID, user: CandidateUser, session: Session, request: Request
) -> InvitationAcceptOut:
    invitation, company = await service.accept_invitation(
        session, user, invitation_id, ip=client_ip(request)
    )
    return InvitationAcceptOut(
        invitation=service.build_invitation(
            invitation, company_name=company.name, contact_method_visible=True
        ),
        company_contacts=CompanyContactsOut(
            company_name=company.name,
            contact_method=invitation.contact_method,
            contact_email=company.contact_email,
            contact_phone=company.contact_phone,
        ),
    )


@router.post(
    "/invitations/{invitation_id}/decline",
    response_model=InvitationOut,
    summary="Отклонить приглашение (с причиной)",
)
async def decline_invitation(
    invitation_id: UUID,
    payload: InvitationDeclineIn,
    user: CandidateUser,
    session: Session,
    request: Request,
) -> InvitationOut:
    invitation = await service.decline_invitation(
        session, user, invitation_id, payload.reason_code, payload.text, ip=client_ip(request)
    )
    return service.build_invitation(invitation)


@router.get(
    "/invitations/{invitation_id}",
    response_model=InvitationOut,
    summary="Приглашение по id (работодатель — псевдоним, кандидат — компания)",
)
async def get_invitation(
    invitation_id: UUID,
    user: Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_EMPLOYER, ROLE_ADMIN))],
    session: Session,
) -> InvitationOut:
    if ROLE_EMPLOYER in user.effective_roles and user.active_role != ROLE_CANDIDATE:
        company, _ = await employers_service.get_or_create_company(session, user)
        invitation = (
            await session.execute(select(Invitation).where(Invitation.id == invitation_id))
        ).scalar_one_or_none()
        if invitation is None or invitation.company_id != company.id:
            raise not_found("Приглашение не найдено")
        return service.build_invitation(invitation, contact_method_visible=True)
    profile = await service.get_candidate_profile(session, user)
    invitation = await service.get_invitation_for_candidate(session, profile, invitation_id)
    return service.build_invitation(
        invitation, contact_method_visible=invitation.status == "accepted"
    )
