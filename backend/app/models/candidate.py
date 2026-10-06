"""Кандидат (PLAN.md §3.3–3.7, §7.4): профиль с шифрованными ПДн, опрос, свершения,
достижения ФСП, подтверждения узлов, материализованный снимок силы и категория.

ПДн (ФИО, телефон, telegram) — только через `EncryptedStr` (`*_enc`, bytea) + blind index
`*_hash` (HMAC от нормализованного значения). В списках, кэше, логах и `llm_call` кандидат
представлен псевдонимом вида «B-7F3A». Категория (`candidate_category`) присваивается
только из тестовой сессии; без неё профиль не `searchable`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.pii import EncryptedStr
from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk

CONFIRMATION_SOURCES = ("test", "fsp", "regular", "self")
PROFILE_SHAPES = ("dot", "I", "T", "P")
FSP_RESULT_TIERS = ("winner", "prize", "finalist", "participant")

# настройки приватности по умолчанию (PLAN.md §5.7 п. 8)
DEFAULT_PRIVACY: dict[str, Any] = {
    "searchable": True,
    "show_city": True,
    "show_fsp": True,
    "show_accomplishments": True,
    "hidden_company_ids": [],
}


class CandidateProfile(TimestampMixin, VersionedMixin, Base):
    """Профиль кандидата 1:1 с `app_user`.

    Атрибуты `full_name` / `phone` / `telegram` в Python — открытые значения, в БД —
    шифртекст; blind-index колонки заполняет сервис через `get_pii_crypto().blind_index`.
    `searchable` — эффективный флаг видимости в банке и подборке: пересчитывается сервисом
    как `privacy.searchable AND категория есть` (PLAN.md §3.7).
    """

    __tablename__ = "candidate_profile"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    pseudonym: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)  # «B-7F3A»
    full_name: Mapped[str | None] = mapped_column("full_name_enc", EncryptedStr)
    full_name_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    phone: Mapped[str | None] = mapped_column("phone_enc", EncryptedStr)
    phone_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    telegram: Mapped[str | None] = mapped_column("telegram_enc", EncryptedStr)
    telegram_hash: Mapped[str | None] = mapped_column(sa.CHAR(64), index=True)
    city: Mapped[str | None] = mapped_column(sa.Text)
    industry_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("industry.code", ondelete="SET NULL")
    )
    # специализация из анкеты (значения — /skills/specializations); пока ФСП id не отдаёт
    # данные, заполняется кандидатом вручную; без неё тест на грейд недоступен (409)
    specialization_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="SET NULL")
    )
    # заявленный стек («заявлено», в ранжировании не участвует) и подтверждённый заданиями
    stack_tags_declared: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    stack_tags_confirmed: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    experience_years: Mapped[int | None] = mapped_column(sa.SmallInteger)
    roles_declared: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    soft_skills_declared: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    # самоописание после sanitize_for_llm (без контактов и ФИО)
    about_sanitized: Mapped[str | None] = mapped_column(sa.Text)
    # приватная зарплатная планка, руб./мес.; работодателю не раскрывается
    salary_floor: Mapped[int | None] = mapped_column(sa.Integer)
    privacy: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
        default=lambda: dict(DEFAULT_PRIVACY),
    )
    searchable: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    fsp_id: Mapped[str | None] = mapped_column(sa.Text, index=True)
    fsp_linked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "salary_floor IS NULL OR salary_floor > 0", name="salary_floor_positive"
        ),
        sa.CheckConstraint(
            "experience_years IS NULL OR experience_years BETWEEN 0 AND 60",
            name="experience_years_range",
        ),
        sa.Index("ix_candidate_profile_searchable", "searchable"),
    )


class SurveyResponse(CreatedAtMixin, Base):
    """Опрос при входе: отрасль, специализация, заявленный грейд и стек, ответы анкеты."""

    __tablename__ = "survey_response"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    industry_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("industry.code", ondelete="SET NULL")
    )
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="RESTRICT"), nullable=False
    )
    claimed_grade: Mapped[str] = mapped_column(sa.Text, nullable=False)
    stack_tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.CheckConstraint("claimed_grade IN ('junior', 'middle', 'senior')", name="grade_known"),
    )


class AchievementClaim(TimestampMixin, Base):
    """Свершение из анкеты (источник `self`): силу узла не меняет, задаёт, что проверять,
    и даёт контекст персонального сценарного задания."""

    __tablename__ = "achievement_claim"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description_sanitized: Mapped[str | None] = mapped_column(sa.Text)
    happened_at: Mapped[date | None] = mapped_column(sa.Date)
    link: Mapped[str | None] = mapped_column(sa.Text)
    result: Mapped[str | None] = mapped_column(sa.Text)


class FspAchievement(CreatedAtMixin, Base):
    """Достижение из реестра ФСП (PLAN.md §3.5); `raw` — исходная запись реестра без ПДн."""

    __tablename__ = "fsp_achievement"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fsp_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    event_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    discipline: Mapped[str] = mapped_column(sa.Text, nullable=False)
    event_type: Mapped[str | None] = mapped_column(sa.Text)
    event_date: Mapped[date | None] = mapped_column(sa.Date)
    # winner | prize | finalist | participant
    result_tier: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'participant'"), default="participant"
    )
    rank: Mapped[int | None] = mapped_column(sa.Integer)
    percentile: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    is_team: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.UniqueConstraint("candidate_id", "event_id", name="uq_fsp_achievement_candidate_event"),
        sa.CheckConstraint(
            "result_tier IN ('winner', 'prize', 'finalist', 'participant')", name="tier_known"
        ),
    )


class SkillConfirmation(CreatedAtMixin, Base):
    """Единица данных модели (PLAN.md §3.3): подтверждение узла с весом и полураспадом.

    `source_ref` — id сессии теста / достижения ФСП / назначения регулярного задания.
    """

    __tablename__ = "skill_confirmation"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    w0: Mapped[Decimal] = mapped_column(sa.Numeric(5, 4), nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)  # test | fsp | regular | self
    source_ref: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    confirmed_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    half_life_days: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.Index("ix_skill_confirmation_candidate_node", "candidate_id", "node_id"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        sa.CheckConstraint("w0 >= 0 AND w0 <= 1", name="w0_range"),
        sa.CheckConstraint("half_life_days > 0", name="half_life_positive"),
        sa.CheckConstraint("source IN ('test', 'fsp', 'regular', 'self')", name="source_known"),
    )


class CandidateSkillState(Base):
    """Материализованный снимок силы узла (PLAN.md §3.3): пересчитывается при новом
    подтверждении и ночной джобой `decay-recompute`; подборка читает только его."""

    __tablename__ = "candidate_skill_state"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="CASCADE"), primary_key=True
    )
    # максимальный уровень, на котором узел активен (S ≥ 0.5); 0 — не активен
    active_level: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("0"), default=0
    )
    # {"1": 0.93, "2": 0.61, "3": 0.0}
    strength_by_level: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    # max_e(w0_e · decay) — свежесть для UI: ≥ 0.8 свежо, 0.5–0.8 тускнеет, < 0.5 погасло
    w_now: Mapped[Decimal] = mapped_column(
        sa.Numeric(5, 4), nullable=False, server_default=sa.text("0"), default=Decimal("0")
    )
    freshest_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    computed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index("ix_candidate_skill_state_node_active", "node_id", "active_level"),
        sa.CheckConstraint("active_level BETWEEN 0 AND 3", name="active_level_range"),
    )


class CandidateCategory(Base):
    """Категория = специализация × грейд (PLAN.md §3.4); присваивается только из
    `test_session`; `history` — опросы, тесты (включая провальные и демо) и смены грейда."""

    __tablename__ = "candidate_category"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="RESTRICT"), nullable=False
    )
    grade: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Q = |A| − C(A) по активным узлам
    q_level: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("0"), default=0
    )
    shape: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'dot'"), default="dot"
    )  # dot | I | T | P
    # сила профиля без потребности (profile_score) — для банка
    strength: Mapped[Decimal] = mapped_column(
        sa.Numeric(6, 4), nullable=False, server_default=sa.text("0"), default=Decimal("0")
    )
    # сессия, из которой присвоена категория (инвариант «только из test_session»)
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("test_session.id", ondelete="SET NULL")
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    grade_change_available_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    upgrade_window_until: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    history: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "candidate_id", "specialization_code", name="uq_candidate_category_spec"
        ),
        sa.Index("ix_candidate_category_spec_grade", "specialization_code", "grade"),
        sa.CheckConstraint("grade IN ('junior', 'middle', 'senior')", name="grade_known"),
        sa.CheckConstraint("shape IN ('dot', 'I', 'T', 'P')", name="shape_known"),
    )


# relationship объявлен после классов, чтобы не плодить циклических импортов
CandidateProfile.confirmations = relationship(
    SkillConfirmation, cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
)
CandidateProfile.categories = relationship(
    CandidateCategory, cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
)


__all__ = [
    "CONFIRMATION_SOURCES",
    "DEFAULT_PRIVACY",
    "FSP_RESULT_TIERS",
    "PROFILE_SHAPES",
    "AchievementClaim",
    "CandidateCategory",
    "CandidateProfile",
    "CandidateSkillState",
    "FspAchievement",
    "SkillConfirmation",
    "SurveyResponse",
]
