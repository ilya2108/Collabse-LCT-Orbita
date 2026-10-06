"""Роутер модуля `employers` (PLAN.md §7.5): компания работодателя (личный/корпоративный
профиль), участники с приглашением без входа на платформу, минимальный CRUD потребностей.

Префикса у роутера нет: пути `/employers/*` и `/needs/*` объявляются полностью
(потребности по плану живут на верхнем уровне). Доступ — роль `employer` (и `admin`);
исключения: просмотр приглашения по токену — публичный (токен и есть секрет),
принятие приглашения — любой аутентифицированный пользователь (роль `employer`
дописывается сервисом).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip
from app.core.db import get_session
from app.core.errors import not_found
from app.core.security import (
    ROLE_ADMIN,
    ROLE_EMPLOYER,
    get_current_user,
    require_consent,
    require_roles,
)
from app.models.employer import Company
from app.models.user import AppUser
from app.modules.employers import service
from app.modules.employers.schemas import (
    CompanyOut,
    CompanyUpdateIn,
    InviteAcceptIn,
    InvitePublicOut,
    MemberInviteIn,
    MemberInviteOut,
    MembersOut,
    NeedIn,
    NeedOut,
    NeedUpdateIn,
)

router = APIRouter(tags=["employers"])

EmployerUser = Annotated[AppUser, Depends(require_roles(ROLE_EMPLOYER, ROLE_ADMIN))]
AnyUser = Annotated[AppUser, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


# --- компания ---------------------------------------------------------------------------


@router.get(
    "/employers/me/company",
    response_model=CompanyOut,
    summary="Моя компания (при первом обращении создаётся личный профиль)",
)
async def get_my_company(user: EmployerUser, session: Session) -> CompanyOut:
    company, my_role = await service.get_or_create_company(session, user)
    members_count = await service.count_active_members(session, company.id)
    return service.build_company(company, my_role, members_count)


@router.put(
    "/employers/me/company",
    response_model=CompanyOut,
    summary="Обновить компанию (название, тип профиля, контакты — ПДн шифруются)",
)
async def update_my_company(
    payload: CompanyUpdateIn, user: EmployerUser, session: Session
) -> CompanyOut:
    require_consent(user)
    company, my_role = await service.get_or_create_company(session, user)
    company = await service.update_company(session, company, my_role, payload.changes())
    return service.build_company(company, my_role)


# --- участники --------------------------------------------------------------------------


@router.get(
    "/employers/me/company/members",
    response_model=MembersOut,
    summary="Участники компании (ПДн приглашённых — маскированные)",
)
async def get_members(user: EmployerUser, session: Session) -> MembersOut:
    company, _ = await service.get_or_create_company(session, user)
    members = await service.list_members(session, company.id)
    return MembersOut(items=[service.build_member(m) for m in members], total=len(members))


@router.post(
    "/employers/me/company/members/invite",
    response_model=MemberInviteOut,
    status_code=201,
    summary="Пригласить эйчара/менеджера (одноразовый токен — только в этом ответе)",
    responses={409: {"description": "company_kind_personal | member_already_invited"}},
)
async def invite_member(
    payload: MemberInviteIn, user: EmployerUser, session: Session, request: Request
) -> MemberInviteOut:
    require_consent(user)
    company, my_role = await service.get_or_create_company(session, user)
    member, token = await service.invite_member(
        session, company, my_role, user, payload, ip=client_ip(request)
    )
    return MemberInviteOut(
        member=service.build_member(member),
        invite_token=token,
        invite_url=service.invite_url_for(token),
    )


@router.delete(
    "/employers/me/company/members/{member_id}",
    status_code=204,
    summary="Отозвать участника или приглашение",
)
async def revoke_member(
    member_id: UUID, user: EmployerUser, session: Session, request: Request
) -> None:
    company, my_role = await service.get_or_create_company(session, user)
    await service.revoke_member(session, company, my_role, user, member_id, ip=client_ip(request))


@router.get(
    "/employers/company/invites/{token}",
    response_model=InvitePublicOut,
    summary="Информация о приглашении по токену (для страницы принятия и бота)",
)
async def get_invite_info(token: str, session: Session) -> InvitePublicOut:
    member = await service.find_member_by_token(session, token)
    if member is None:
        raise not_found("Приглашение не найдено или уже использовано")
    company = await session.get(Company, member.company_id)
    if company is None:
        raise not_found("Приглашение не найдено или уже использовано")
    return InvitePublicOut(company_name=company.name, role=member.role, status=member.status)


@router.post(
    "/employers/company/invites/accept",
    response_model=CompanyOut,
    summary="Принять приглашение в компанию (роль employer дописывается автоматически)",
)
async def accept_invite(
    payload: InviteAcceptIn, user: AnyUser, session: Session, request: Request
) -> CompanyOut:
    company, member = await service.accept_invite(
        session, user, payload.token, ip=client_ip(request)
    )
    return service.build_company(company, member.role)


# --- потребности ------------------------------------------------------------------------


@router.post(
    "/needs",
    response_model=NeedOut,
    status_code=201,
    summary="Создать потребность (зарплата «от–до» обязательна)",
)
async def create_need(payload: NeedIn, user: EmployerUser, session: Session) -> NeedOut:
    company, _ = await service.get_or_create_company(session, user)
    need = await service.create_need(session, company, payload)
    return service.build_need(need)


@router.get("/needs", summary="Потребности моей компании")
async def get_needs(user: EmployerUser, session: Session) -> dict[str, Any]:
    company, _ = await service.get_or_create_company(session, user)
    needs = await service.list_needs(session, company.id)
    return {"items": [service.build_need(n) for n in needs], "total": len(needs)}


@router.get("/needs/{need_id}", response_model=NeedOut, summary="Потребность по id")
async def get_need(need_id: UUID, user: EmployerUser, session: Session) -> NeedOut:
    company, _ = await service.get_or_create_company(session, user)
    need = await service.get_company_need(session, company.id, need_id)
    return service.build_need(need)


@router.put("/needs/{need_id}", response_model=NeedOut, summary="Обновить потребность")
async def put_need(
    need_id: UUID, payload: NeedUpdateIn, user: EmployerUser, session: Session
) -> NeedOut:
    company, _ = await service.get_or_create_company(session, user)
    need = await service.get_company_need(session, company.id, need_id)
    need = await service.update_need(session, need, payload.changes())
    return service.build_need(need)
