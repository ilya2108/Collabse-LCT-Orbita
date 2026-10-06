"""Сервис телеграм-бота: одноразовые коды привязки, привязка чатов, исходящие сообщения.

ПДн-правила (152-ФЗ, правила организаторов):
- `tg_chat_id` — технический идентификатор, в БД хранится открыто, но в логи НЕ пишется;
- перед любой отправкой текст проходит `sanitize_for_llm` (email/телефоны/ФИО →
  плейсхолдеры); исключение — сообщение кандидату с контактами компании ПОСЛЕ принятия
  приглашения (`allow_contacts=True`), и тогда в журнал `tg_outbox` пишется копия
  с уже замаскированными контактами (`journal_text`);
- в боте работодателя кандидат до принятия приглашения — только псевдоним.
"""

from __future__ import annotations

import base64
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import get_pii_crypto
from app.models.employer import CompanyMember
from app.models.tgbot import TgLink, TgLinkToken, TgOutboxMessage
from app.modules.llm.sanitizer import sanitize_for_llm
from app.modules.tgbot.config import get_tgbot_settings

LINK_TOKEN_BYTES = 24  # token_urlsafe(24) → 32 символа, лимит deep-link Telegram — 64

# виды исходящих сообщений (kind в tg_outbox)
KIND_WEEKLY_DIGEST = "digest_weekly"
KIND_MANUAL_DIGEST = "digest_manual"
KIND_INVITATION_PUSH = "invitation_push"
KIND_REPLY = "reply"


# --- одноразовые коды привязки ----------------------------------------------------------


def deep_link_for(token: str) -> str:
    """Deep-link привязки: https://t.me/<бот>?start=<код>."""
    username = get_tgbot_settings().telegram_bot_username.lstrip("@")
    return f"https://t.me/{username}?start={token}"


async def create_link_token(
    session: AsyncSession, user_id: uuid.UUID, role: str
) -> tuple[str, TgLinkToken]:
    """Выпускает одноразовый код привязки; в БД — только HMAC (код показывается один раз)."""
    token = secrets.token_urlsafe(LINK_TOKEN_BYTES)
    row = TgLinkToken(
        user_id=user_id,
        role=role,
        token_hash=get_pii_crypto().hmac_hex(token),
        expires_at=datetime.now(UTC)
        + timedelta(minutes=get_tgbot_settings().tg_link_token_ttl_minutes),
    )
    session.add(row)
    await session.commit()
    return token, row


def link_token_is_valid(row: TgLinkToken | None, now: datetime | None = None) -> bool:
    """Код пригоден к использованию: существует, не использован, не протух."""
    if row is None or row.used_at is not None:
        return False
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at >= (now or datetime.now(UTC))


async def consume_link_token(session: AsyncSession, token: str) -> TgLinkToken | None:
    """Находит код по HMAC и помечает использованным; None — кода нет/протух/б. у."""
    token_hash = get_pii_crypto().hmac_hex(token)
    row = (
        await session.execute(select(TgLinkToken).where(TgLinkToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None or not link_token_is_valid(row):
        return None
    row.used_at = datetime.now(UTC)
    return row


# --- привязка чатов ---------------------------------------------------------------------


async def find_link(session: AsyncSession, tg_chat_id: int) -> TgLink | None:
    return (
        await session.execute(select(TgLink).where(TgLink.tg_chat_id == tg_chat_id))
    ).scalar_one_or_none()


async def find_link_for_user(session: AsyncSession, user_id: uuid.UUID) -> TgLink | None:
    """Привязка текущего пользователя платформы (для статуса в настройках)."""
    rows = await session.execute(
        select(TgLink).where(TgLink.user_id == user_id).order_by(TgLink.created_at.desc())
    )
    return rows.scalars().first()


async def bind_chat(
    session: AsyncSession,
    tg_chat_id: int,
    *,
    role: str,
    user_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
) -> TgLink:
    """Привязывает чат (одна строка на чат; повторный /start перепривязывает)."""
    link = await find_link(session, tg_chat_id)
    if link is None:
        link = TgLink(tg_chat_id=tg_chat_id, user_id=user_id, member_id=member_id, role=role)
        session.add(link)
    else:
        link.user_id = user_id
        link.member_id = member_id
        link.role = role
    await session.commit()
    return link


async def bind_member_chat(session: AsyncSession, tg_chat_id: int, member: CompanyMember) -> TgLink:
    """«HR через Telegram»: чат привязывается к участнику компании БЕЗ аккаунта платформы.

    Строка `company_member` не меняется (invite-токен остаётся действителен для веба:
    HR может позже зарегистрироваться и принять приглашение на платформе).
    """
    return await bind_chat(session, tg_chat_id, role="employer", member_id=member.id)


# --- исходящие сообщения ----------------------------------------------------------------


@dataclass
class OutgoingMessage:
    """Исходящее сообщение бота: текст + inline-кнопки [{text, callback_data}]."""

    tg_chat_id: int
    text: str
    kind: str = KIND_REPLY
    ref_id: uuid.UUID | None = None
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
    # журнальная копия для tg_outbox (если ПДн надо замаскировать сильнее, чем в отправке)
    journal_text: str | None = None


class BotTransport(Protocol):
    """Отправка в Telegram; в stub-режиме транспорта нет (None)."""

    async def send(self, msg: OutgoingMessage, text: str) -> None: ...


class AiogramTransport:
    """Реальная отправка через aiogram (режим polling)."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def send(self, msg: OutgoingMessage, text: str) -> None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = None
        if msg.buttons:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(**button) for button in row] for row in msg.buttons
                ]
            )
        await self._bot.send_message(msg.tg_chat_id, text, reply_markup=markup)


async def deliver(
    session: AsyncSession,
    transport: BotTransport | None,
    msg: OutgoingMessage,
    *,
    allow_contacts: bool = False,
) -> TgOutboxMessage:
    """Единственная точка отправки: санитайзер ПДн + журнал tg_outbox + транспорт.

    Без `allow_contacts` отправляемый текст проходит `sanitize_for_llm`; с ним (контакты
    компании кандидату после принятия) — отправляется как есть, а в журнал пишется
    `journal_text` (копия с маскированными контактами) либо санированный текст.
    """
    text = msg.text if allow_contacts else sanitize_for_llm(msg.text)
    journal = msg.journal_text if msg.journal_text is not None else sanitize_for_llm(msg.text)
    status = "stub"
    if transport is not None:
        try:
            await transport.send(msg, text)
            status = "sent"
        except Exception:  # сбой сети не должен ронять планировщик/обработчик
            status = "failed"
    row = TgOutboxMessage(
        tg_chat_id=msg.tg_chat_id,
        kind=msg.kind,
        ref_id=msg.ref_id,
        text=journal,
        buttons=msg.buttons or None,
        status=status,
    )
    session.add(row)
    await session.commit()
    return row


async def already_sent(
    session: AsyncSession,
    tg_chat_id: int,
    kind: str,
    *,
    ref_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> bool:
    """Дедупликация: было ли сообщение (kind [, ref_id] [, не раньше since]) в этот чат."""
    stmt = select(TgOutboxMessage.id).where(
        TgOutboxMessage.tg_chat_id == tg_chat_id, TgOutboxMessage.kind == kind
    )
    if ref_id is not None:
        stmt = stmt.where(TgOutboxMessage.ref_id == ref_id)
    if since is not None:
        stmt = stmt.where(TgOutboxMessage.created_at >= since)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


# --- callback-данные (лимит Telegram — 64 байта) ----------------------------------------


def pack_uuid(value: uuid.UUID) -> str:
    """UUID → 22 символа urlsafe base64 без '=' (вписывается в callback_data)."""
    return base64.urlsafe_b64encode(value.bytes).decode().rstrip("=")


def unpack_uuid(packed: str) -> uuid.UUID:
    return uuid.UUID(bytes=base64.urlsafe_b64decode(packed + "=="))
