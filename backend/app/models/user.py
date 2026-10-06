"""`app_user` — зеркало пользователей Keycloak, роли и согласия 152-ФЗ (PLAN.md §7.4).

Keycloak только аутентифицирует; роли `candidate` / `employer` и согласия хранятся здесь,
`admin` приходит только из токена Keycloak (`token_roles`) и в БД не записывается.
Email — ПДн: в БД лежит Fernet-шифртекст `email_enc` + blind index `email_hash` (HMAC от
нормализованного значения) для поиска и «усыновления» сид-записей (`keycloak_sub` = `seed:…`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pii import EncryptedStr
from app.models.base import Base, TimestampMixin, uuid_pk


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"
    # разрешает атрибут token_roles без Mapped[] (SQLAlchemy 2: иначе ошибка маппинга)
    __allow_unmapped__ = True

    # роли из access-токена текущего запроса: `admin` живёт ТОЛЬКО здесь и в БД не
    # записывается (core.security.sync_app_user); не колонка, значение не переживает запрос
    token_roles: tuple[str, ...] = ()

    id: Mapped[uuid.UUID] = uuid_pk()
    keycloak_sub: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    username: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # атрибут `email` в Python — открытое значение; в БД — колонка email_enc (bytea)
    email: Mapped[str | None] = mapped_column("email_enc", EncryptedStr)
    email_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    email_verified: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    # candidate | employer | admin | NULL (роль ещё не выбрана)
    active_role: Mapped[str | None] = mapped_column(sa.Text)
    # согласие на обработку ПДн: момент, версия политики, момент согласия на публикацию
    # профиля работодателям (без него кандидат не searchable)
    consent_pdn_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    consent_policy_version: Mapped[str | None] = mapped_column(sa.Text)
    consent_publish_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    # отзыв согласия / удаление аккаунта: ПДн стираются, строка остаётся для FK и аудита
    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "active_role IS NULL OR active_role IN ('candidate', 'employer', 'admin')",
            name="active_role_known",
        ),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def effective_roles(self) -> list[str]:
        """Роли для авторизации и `GET /auth/me`: сохранённые в БД ∪ роли токена."""
        return sorted(set(self.roles or []) | set(self.token_roles or ()))

    def has_role(self, role: str) -> bool:
        return role in self.effective_roles
