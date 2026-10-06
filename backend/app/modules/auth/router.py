"""Роутер модуля `auth` (PLAN.md §7.5): профиль текущего пользователя, выбор роли при первом
входе, согласие 152-ФЗ и удаление аккаунта. Аутентификация — JWT Keycloak
(`core.security.get_current_user`), роли — из БД."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip
from app.core.db import get_session
from app.core.security import get_current_user
from app.models.user import AppUser
from app.modules.auth import service
from app.modules.auth.schemas import ConsentIn, MeResponse, RoleIn

router = APIRouter(prefix="/auth", tags=["auth"])

CurrentUser = Annotated[AppUser, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/me", response_model=MeResponse, summary="Текущий пользователь, роли и согласия")
async def get_me(user: CurrentUser) -> MeResponse:
    return service.build_me(user)


@router.post(
    "/role",
    response_model=MeResponse,
    summary="Выбор роли при первом входе (candidate | employer)",
    responses={409: {"description": "role_already_set — роль уже выбрана"}},
)
async def choose_role(
    payload: RoleIn, user: CurrentUser, session: Session, request: Request
) -> MeResponse:
    user = await service.choose_role(session, user, payload.role, ip=client_ip(request))
    return service.build_me(user)


@router.post(
    "/consent",
    response_model=MeResponse,
    summary="Согласие на обработку ПДн (версия политики) и на публикацию профиля",
)
async def give_consent(
    payload: ConsentIn, user: CurrentUser, session: Session, request: Request
) -> MeResponse:
    user = await service.give_consent(
        session,
        user,
        policy_version=payload.policy_version,
        publish=payload.publish,
        ip=client_ip(request),
    )
    return service.build_me(user)


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отзыв согласия и удаление персональных данных",
)
async def delete_account(user: CurrentUser, session: Session, request: Request) -> Response:
    await service.delete_account(session, user, ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
