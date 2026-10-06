"""Телеграм-бот «ФСП.Орбита» (один бот, роли кандидата и работодателя/HR):

- `tg_link` — привязка Telegram-чата к пользователю платформы ИЛИ к участнику компании
  без аккаунта (`company_member.user_id IS NULL` — «HR через Telegram»); `tg_chat_id` —
  технический идентификатор, не ПДн (шифрование не требуется), но в логи не пишется;
- `tg_link_token` — одноразовые коды привязки из `POST /auth/telegram/link-token`:
  в БД хранится только HMAC кода (как у invite-токенов участников компании);
- `tg_outbox` — журнал исходящих сообщений: в режиме `TELEGRAM_BOT_MODE=stub` сообщения
  только пишутся сюда (стенд и тесты работают без токена бота), в `polling` — журнал
  плюс реальная отправка. Поле `text` — журнальная копия, ПДн в ней маскируются
  вызывающим кодом ДО записи; пара (kind, ref_id, chat) служит дедупликацией пушей.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, uuid_pk

TG_LINK_ROLES = ("candidate", "employer")
TG_OUTBOX_STATUSES = ("stub", "sent", "failed")


class TgLink(CreatedAtMixin, Base):
    """Привязка Telegram-чата: ровно одна строка на чат (повторный /start перепривязывает).

    Либо `user_id` (пользователь платформы, роль из токена привязки), либо `member_id`
    (приглашённый HR без аккаунта); после регистрации HR на платформе чат можно
    перепривязать обычным кодом — строка обновится на `user_id`.
    """

    __tablename__ = "tg_link"

    id: Mapped[uuid.UUID] = uuid_pk()
    tg_chat_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("company_member.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)  # candidate | employer

    __table_args__ = (
        sa.CheckConstraint("role IN ('candidate', 'employer')", name="role_known"),
        sa.CheckConstraint("user_id IS NOT NULL OR member_id IS NOT NULL", name="subject_present"),
    )


class TgLinkToken(CreatedAtMixin, Base):
    """Одноразовый код привязки чата; сам код показывается один раз, в БД — HMAC."""

    __tablename__ = "tg_link_token"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)  # candidate | employer
    token_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (sa.CheckConstraint("role IN ('candidate', 'employer')", name="role_known"),)


class TgOutboxMessage(Base):
    """Журнал исходящих сообщений бота (и «отправка» в stub-режиме).

    `ref_id` — сущность-источник (например, id приглашения) для дедупликации пушей;
    `text` — журнальная копия с уже замаскированными ПДн.
    """

    __tablename__ = "tg_outbox"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    buttons: Mapped[list[Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'stub'"), default="stub"
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("status IN ('stub', 'sent', 'failed')", name="status_known"),
        sa.Index("ix_tg_outbox_chat_kind", "tg_chat_id", "kind"),
        sa.Index("ix_tg_outbox_kind_ref", "kind", "ref_id"),
    )


__all__ = [
    "TG_LINK_ROLES",
    "TG_OUTBOX_STATUSES",
    "TgLink",
    "TgLinkToken",
    "TgOutboxMessage",
]
