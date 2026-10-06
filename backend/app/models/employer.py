"""Работодатель и подбор (PLAN.md §5, §7.4): компания, потребности и их версии, пины,
вакансии, приглашения, отклики, доступ к контактам и регулярные задания.

Инварианты уровня БД: `salary_from <= salary_to` и `salary_from > 0` (вакансия,
приглашение, потребность); одно открытое приглашение на пару (компания, кандидат) —
partial unique index `WHERE status IN ('sent', 'viewed')`; FK приглашения только на
`candidate_profile` (импортированные записи жюри пригласить нельзя по построению).
Контакты компании — ПДн: `EncryptedStr` + blind index, как у кандидата.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.pii import EncryptedStr
from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk

COMPANY_KINDS = ("personal", "corporate")
MEMBER_ROLES = ("owner", "hr", "hiring_manager")
MEMBER_STATUSES = ("invited", "active", "revoked")
INVITE_CHANNELS = ("email", "telegram")
# стадии мини-CRM (воронка «потребность × кандидат»); invited/accepted проставляются
# только сервисом приглашений, остальные работодатель двигает вручную
PIPELINE_STAGES = ("shortlist", "invited", "accepted", "interview", "offer", "hired", "declined")
NEED_STATUSES = ("draft", "active", "archived")
NEED_VERSION_MODES = ("verified", "unverified")
WORK_FORMATS = ("remote", "office", "hybrid")
VACANCY_STATUSES = ("draft", "published", "closed")
INVITATION_STATUSES = ("sent", "viewed", "accepted", "declined", "expired")
INVITATION_OPEN_STATUSES = ("sent", "viewed")
APPLICATION_STATUSES = ("sent", "viewed", "accepted", "declined", "withdrawn")
CONTACT_GRANT_BASES = ("invitation", "application")
REGULAR_TASK_STATUSES = ("active", "closed")
ASSIGNMENT_STATUSES = ("queued", "active", "answered", "graded", "expired")

_SALARY_CHECK = "salary_from > 0 AND salary_from <= salary_to"
_SALARY_CHECK_NULLABLE = (
    "(salary_from IS NULL OR salary_from > 0) AND "
    "(salary_from IS NULL OR salary_to IS NULL OR salary_from <= salary_to)"
)


class Company(TimestampMixin, VersionedMixin, Base):
    """Компания работодателя (1:1 с `app_user`); `is_service` — служебная «Проверка жюри»."""

    __tablename__ = "company"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inn: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    industry_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("industry.code", ondelete="SET NULL")
    )
    website: Mapped[str | None] = mapped_column(sa.Text)
    contact_email: Mapped[str | None] = mapped_column("contact_email_enc", EncryptedStr)
    contact_email_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    contact_phone: Mapped[str | None] = mapped_column("contact_phone_enc", EncryptedStr)
    contact_phone_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    is_service: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    # личный профиль (по умолчанию, создаётся при первом входе работодателя)
    # или корпоративный (команда с участниками company_member)
    kind: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'personal'"), default="personal"
    )

    __table_args__ = (sa.CheckConstraint("kind IN ('personal', 'corporate')", name="kind_known"),)


class CompanyMember(TimestampMixin, Base):
    """Участник компании: владелец, эйчар или нанимающий менеджер.

    `user_id` NULLABLE: эйчара можно пригласить без входа на платформу — у него есть
    только зашифрованные контакты и одноразовый invite-токен (в БД — HMAC-хэш токена,
    сам токен показывается один раз в ответе на приглашение). После принятия приглашения
    `user_id` привязывается, статус становится `active`, хэш токена стирается.
    ПДн приглашённого (ФИО, email, telegram) — `EncryptedStr` + blind index, как у кандидата.
    """

    __tablename__ = "company_member"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)  # owner | hr | hiring_manager
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'invited'"), default="invited"
    )
    full_name: Mapped[str | None] = mapped_column("full_name_enc", EncryptedStr)
    full_name_hash: Mapped[str | None] = mapped_column(sa.CHAR(64))
    email: Mapped[str | None] = mapped_column("email_enc", EncryptedStr)
    email_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    telegram: Mapped[str | None] = mapped_column("telegram_enc", EncryptedStr)
    telegram_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    invite_channel: Mapped[str | None] = mapped_column(sa.Text)  # email | telegram
    # HMAC-SHA256 одноразового токена приглашения; NULL после принятия или отзыва
    invite_token_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )
    invited_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        # у пользователя не больше одной строки участия в компании
        sa.Index(
            "uq_company_member_company_user",
            "company_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("user_id IS NOT NULL"),
        ),
        sa.CheckConstraint("role IN ('owner', 'hr', 'hiring_manager')", name="role_known"),
        sa.CheckConstraint("status IN ('invited', 'active', 'revoked')", name="status_known"),
        sa.CheckConstraint(
            "invite_channel IS NULL OR invite_channel IN ('email', 'telegram')",
            name="channel_known",
        ),
    )


class EmployerNeed(TimestampMixin, VersionedMixin, Base):
    """Потребность работодателя: текст + «задачи на первые 3 месяца» → подграф и версии."""

    __tablename__ = "employer_need"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    raw_text_sanitized: Mapped[str | None] = mapped_column(sa.Text)
    first_months_tasks: Mapped[str | None] = mapped_column(sa.Text)
    specialization_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="SET NULL")
    )
    grade: Mapped[str | None] = mapped_column(sa.Text)
    stack_tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    industry_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("industry.code", ondelete="SET NULL")
    )
    salary_from: Mapped[int | None] = mapped_column(sa.Integer)
    salary_to: Mapped[int | None] = mapped_column(sa.Integer)
    format: Mapped[str | None] = mapped_column(sa.Text)  # remote | office | hybrid
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'"), default="draft"
    )

    versions: Mapped[list[NeedVersion]] = relationship(
        back_populates="need",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="NeedVersion.version",
    )

    __table_args__ = (
        sa.CheckConstraint(_SALARY_CHECK_NULLABLE, name="salary_range"),
        sa.CheckConstraint(
            "grade IS NULL OR grade IN ('junior', 'middle', 'senior')", name="grade_known"
        ),
        sa.CheckConstraint(
            "format IS NULL OR format IN ('remote', 'office', 'hybrid')", name="format_known"
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name="status_known"),
    )


class NeedVersion(Base):
    """Версия подборки: снимок подграфа, фильтров и результата (дифф между версиями).

    `mode = unverified` создаётся только из `pair_run` и в обычных списках не показывается.
    """

    __tablename__ = "need_version"

    id: Mapped[uuid.UUID] = uuid_pk()
    need_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("employer_need.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    mode: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'verified'"), default="verified"
    )
    # {"must": ["be.B1", ...], "nice": [...], "bridges": [...]}
    subgraph: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    # [{candidate_id | imported_id, score, breakdown, explanation}]
    result: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    need: Mapped[EmployerNeed] = relationship(back_populates="versions")

    __table_args__ = (
        sa.UniqueConstraint("need_id", "version", name="uq_need_version_need_version"),
        sa.CheckConstraint("mode IN ('verified', 'unverified')", name="mode_known"),
        sa.CheckConstraint("version > 0", name="version_positive"),
    )


class NeedPin(CreatedAtMixin, Base):
    """Шорт-лист: закреплённый кандидат в потребности."""

    __tablename__ = "need_pin"

    need_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("employer_need.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), primary_key=True
    )
    note: Mapped[str | None] = mapped_column(sa.Text)


class Vacancy(TimestampMixin, VersionedMixin, Base):
    """Публикуемая вакансия: зарплата «от–до» обязательна."""

    __tablename__ = "vacancy"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    need_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("employer_need.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    specialization_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="SET NULL")
    )
    grade: Mapped[str | None] = mapped_column(sa.Text)
    salary_from: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    salary_to: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    format: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'"), default="draft"
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(_SALARY_CHECK, name="salary_range"),
        sa.CheckConstraint(
            "grade IS NULL OR grade IN ('junior', 'middle', 'senior')", name="grade_known"
        ),
        sa.CheckConstraint(
            "format IS NULL OR format IN ('remote', 'office', 'hybrid')", name="format_known"
        ),
        sa.CheckConstraint("status IN ('draft', 'published', 'closed')", name="status_known"),
        sa.Index("ix_vacancy_status_spec", "status", "specialization_code"),
    )


class Invitation(CreatedAtMixin, Base):
    """Приглашение от работодателя — неизменяемый снимок предложения и объяснения (§5.7)."""

    __tablename__ = "invitation"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    need_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("employer_need.id", ondelete="SET NULL")
    )
    vacancy_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("vacancy.id", ondelete="SET NULL")
    )
    # встречное задание внутри приглашения (в лимиты назначений не входит)
    regular_task_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("regular_task.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    offer_text: Mapped[str | None] = mapped_column(sa.Text)
    salary_from: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    salary_to: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    contact_method: Mapped[str | None] = mapped_column(sa.Text)
    # «почему именно вы» — тот же текст, что видел работодатель
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'sent'"), default="sent"
    )
    sent_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    viewed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    decline_reason_code: Mapped[str | None] = mapped_column(sa.Text)
    decline_text: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.CheckConstraint(_SALARY_CHECK, name="salary_range"),
        sa.CheckConstraint(
            "status IN ('sent', 'viewed', 'accepted', 'declined', 'expired')", name="status_known"
        ),
        # одно открытое приглашение на пару (компания, кандидат)
        sa.Index(
            "uq_invitation_open_pair",
            "company_id",
            "candidate_id",
            unique=True,
            postgresql_where=sa.text("status IN ('sent', 'viewed')"),
        ),
        sa.Index("ix_invitation_candidate_status", "candidate_id", "status"),
        sa.Index("ix_invitation_company_status", "company_id", "status"),
        sa.Index("ix_invitation_need_id", "need_id"),
    )


class Application(CreatedAtMixin, Base):
    """Отклик кандидата на вакансию."""

    __tablename__ = "application"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("vacancy.id", ondelete="CASCADE"), nullable=False
    )
    cover_text: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'sent'"), default="sent"
    )
    sent_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    viewed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("candidate_id", "vacancy_id", name="uq_application_candidate_vacancy"),
        sa.Index("ix_application_vacancy_status", "vacancy_id", "status"),
        sa.CheckConstraint(
            "status IN ('sent', 'viewed', 'accepted', 'declined', 'withdrawn')",
            name="status_known",
        ),
    )


class ContactGrant(Base):
    """Право работодателя на контакты кандидата: только через него работает `reveal_contacts()`."""

    __tablename__ = "contact_grant"

    id: Mapped[uuid.UUID] = uuid_pk()
    employer_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    basis: Mapped[str] = mapped_column(sa.Text, nullable=False)  # invitation | application
    ref_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("basis", "ref_id", name="uq_contact_grant_basis_ref"),
        sa.Index("ix_contact_grant_employer_candidate", "employer_user_id", "candidate_id"),
        sa.CheckConstraint("basis IN ('invitation', 'application')", name="basis_known"),
    )


class ContactRevealAudit(Base):
    """Факт расшифровки контактов (152-ФЗ): кто, чьи, когда, по какому праву."""

    __tablename__ = "contact_reveal_audit"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("contact_grant.id", ondelete="SET NULL")
    )
    employer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="SET NULL")
    )
    fields: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    ip: Mapped[str | None] = mapped_column(INET)
    revealed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (sa.Index("ix_contact_reveal_audit_candidate_id", "candidate_id"),)


class RegularTask(TimestampMixin, Base):
    """Регулярное задание («Задание недели») по одному узлу; `company_id` NULL — платформенное."""

    __tablename__ = "regular_task"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("company.id", ondelete="CASCADE")
    )
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # {statement, ...} — либо текст работодателя, либо вариант по template_id
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("task_template.id", ondelete="SET NULL")
    )
    rubric: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'"), default="active"
    )
    active_from: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    active_to: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    # срок ответа после назначения, дней (на стенде — DEMO_REGULAR_TASK_DUE_SECONDS)
    due_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("7"), default=7
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )

    __table_args__ = (
        sa.Index("ix_regular_task_node_status", "node_id", "status"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        sa.CheckConstraint("status IN ('active', 'closed')", name="status_known"),
        sa.CheckConstraint("due_days > 0", name="due_days_positive"),
    )


class RegularTaskAssignment(CreatedAtMixin, Base):
    """Назначение задания кандидату; не более одного `active` на кандидата (сервис)."""

    __tablename__ = "regular_task_assignment"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("regular_task.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'"), default="active"
    )
    assigned_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    due_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    grading: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("skill_confirmation.id", ondelete="SET NULL")
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "task_id", "candidate_id", name="uq_regular_task_assignment_task_candidate"
        ),
        sa.Index("ix_regular_task_assignment_candidate_status", "candidate_id", "status"),
        sa.CheckConstraint(
            "status IN ('queued', 'active', 'answered', 'graded', 'expired')", name="status_known"
        ),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
    )


class NeedPipelineEntry(TimestampMixin, Base):
    """Мини-CRM: позиция кандидата в воронке потребности (одна строка на пару).

    До принятия приглашения кандидат в выдаче pipeline представлен только псевдонимом
    и маскированными полями; контакты раскрываются исключительно через
    `GET /invitations/{id}/contacts` при наличии `contact_grant` (правило организаторов).
    """

    __tablename__ = "need_pipeline_entry"

    id: Mapped[uuid.UUID] = uuid_pk()
    need_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("employer_need.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'shortlist'"), default="shortlist"
    )
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("invitation.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("need_id", "candidate_id", name="uq_need_pipeline_entry_pair"),
        sa.Index("ix_need_pipeline_entry_need_stage", "need_id", "stage"),
        sa.CheckConstraint(
            "stage IN ('shortlist', 'invited', 'accepted', 'interview', "
            "'offer', 'hired', 'declined')",
            name="stage_known",
        ),
    )


class PipelineStageEvent(Base):
    """История переходов стадии pipeline (append-only, для аудита воронки)."""

    __tablename__ = "pipeline_stage_event"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("need_pipeline_entry.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_stage: Mapped[str | None] = mapped_column(sa.Text)
    to_stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )
    comment: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )


__all__ = [
    "APPLICATION_STATUSES",
    "ASSIGNMENT_STATUSES",
    "COMPANY_KINDS",
    "CONTACT_GRANT_BASES",
    "INVITATION_OPEN_STATUSES",
    "INVITATION_STATUSES",
    "INVITE_CHANNELS",
    "MEMBER_ROLES",
    "MEMBER_STATUSES",
    "NEED_STATUSES",
    "NEED_VERSION_MODES",
    "PIPELINE_STAGES",
    "REGULAR_TASK_STATUSES",
    "VACANCY_STATUSES",
    "WORK_FORMATS",
    "Application",
    "Company",
    "CompanyMember",
    "ContactGrant",
    "ContactRevealAudit",
    "EmployerNeed",
    "Invitation",
    "NeedPin",
    "NeedPipelineEntry",
    "NeedVersion",
    "PipelineStageEvent",
    "RegularTask",
    "RegularTaskAssignment",
    "Vacancy",
]
