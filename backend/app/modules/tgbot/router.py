"""Роутер модуля tgbot. Префикса нет — пути объявлены полностью (как у employers/matching):

- `POST /auth/telegram/link-token` — одноразовый код привязки чата (любой пользователь
  с ролью candidate/employer; код показывается один раз, в БД — HMAC);
- `POST /internal/digest/run` — немедленная рассылка дайджеста всем привязанным чатам
  (X-Internal-Token; демо «прислать сейчас»);
- `GET /internal/tgbot/outbox` — журнал исходящих сообщений бота (проверка stub-режима
  на демо и в тестах; ПДн в текстах уже замаскированы).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import forbidden
from app.core.security import (
    ROLE_CANDIDATE,
    ROLE_EMPLOYER,
    get_current_user,
    require_internal_token,
)
from app.models.tgbot import TgOutboxMessage
from app.models.user import AppUser
from app.modules.tgbot import scheduler as tg_scheduler
from app.modules.tgbot import service as tg_service
from app.modules.tgbot.config import get_tgbot_settings
from app.modules.tgbot.schemas import (
    DigestRunOut,
    LinkStatusOut,
    LinkTokenIn,
    LinkTokenOut,
    OutboxItemOut,
    OutboxOut,
)

router = APIRouter(tags=["tgbot"])

CurrentUser = Annotated[AppUser, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/auth/telegram/link-token",
    response_model=LinkTokenOut,
    status_code=201,
    summary="Одноразовый код привязки Telegram-чата (показывается один раз)",
)
async def create_telegram_link_token(
    user: CurrentUser, session: Session, payload: LinkTokenIn | None = None
) -> LinkTokenOut:
    role = (payload.role if payload else None) or user.active_role
    if role not in (ROLE_CANDIDATE, ROLE_EMPLOYER):
        selectable = [r for r in user.effective_roles if r in (ROLE_CANDIDATE, ROLE_EMPLOYER)]
        role = selectable[0] if selectable else None
    if role is None or role not in user.effective_roles:
        raise forbidden("Привязка Telegram доступна после выбора роли candidate или employer")
    token, row = await tg_service.create_link_token(session, user.id, role)
    return LinkTokenOut(
        code=token,
        deep_link=tg_service.deep_link_for(token),
        role=role,  # type: ignore[arg-type] — сужено проверкой выше
        expires_at=row.expires_at,
    )


@router.get(
    "/auth/telegram/me",
    response_model=LinkStatusOut,
    summary="Статус привязки Telegram текущего пользователя",
)
async def telegram_link_status(user: CurrentUser, session: Session) -> LinkStatusOut:
    link = await tg_service.find_link_for_user(session, user.id)
    if link is None:
        return LinkStatusOut(linked=False)
    # username в Telegram не сохраняем (ПДн-минимизация) — фронт показывает факт привязки.
    return LinkStatusOut(
        linked=True,
        username_masked=None,
        linked_at=link.created_at,
        digest_enabled=True,
    )


@router.post(
    "/internal/digest/run",
    response_model=DigestRunOut,
    dependencies=[Depends(require_internal_token)],
    summary="Разослать дайджест всем привязанным чатам сейчас (демо)",
)
async def run_digest_now(session: Session) -> DigestRunOut:
    settings = get_tgbot_settings()
    transport = None
    if settings.telegram_bot_mode == "polling":
        from aiogram import Bot

        transport = tg_service.AiogramTransport(Bot(token=settings.telegram_bot_token))
    sent = await tg_scheduler.run_digest_for_all(
        session, transport, kind=tg_service.KIND_MANUAL_DIGEST
    )
    return DigestRunOut(sent=sent, mode=settings.telegram_bot_mode)


@router.get(
    "/internal/tgbot/outbox",
    response_model=OutboxOut,
    dependencies=[Depends(require_internal_token)],
    summary="Журнал исходящих сообщений бота (stub-отправка и дедупликация)",
)
async def list_outbox(session: Session, limit: int = 50) -> OutboxOut:
    rows = (
        (
            await session.execute(
                select(TgOutboxMessage)
                .order_by(TgOutboxMessage.id.desc())
                .limit(min(max(limit, 1), 200))
            )
        )
        .scalars()
        .all()
    )
    items = [
        OutboxItemOut(
            id=row.id,
            tg_chat_id=row.tg_chat_id,
            kind=row.kind,
            text=row.text,
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return OutboxOut(items=items, total=len(items))
