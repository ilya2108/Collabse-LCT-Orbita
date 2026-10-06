"""Тестирование (PLAN.md §4, §7.4): шаблоны «узел × уровень», пул вариантов, сессии,
выданные задания и история грейда.

Инварианты уровня БД: вариант выдаётся кандидату один раз — `UNIQUE (candidate_id,
variant_id)` на `test_item` (candidate_id денормализован из сессии); позиция задания
уникальна внутри сессии. Остальные инварианты (следующее задание только после ответа,
серверный дедлайн, категория только из сессии) — в сервисах `assessment`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, uuid_pk

TEMPLATE_KINDS = ("mcq_reasoned", "code_review", "short_design", "scenario_personal")
TEMPLATE_STATUSES = ("draft", "reviewed", "retired")
VARIANT_STATUSES = ("active", "retired", "rejected")
SESSION_MODES = ("full", "demo", "seed")
SESSION_KINDS = ("initial", "retest_up", "retest_down")
SESSION_STATUSES = ("in_progress", "finished", "expired", "abandoned")
SESSION_OUTCOMES = ("passed", "failed", "strong")
# телеметрия раннера теста: только тайминги и длины, текстов ответов здесь нет
EVENT_KINDS = ("paste", "blur", "focus", "item_shown", "first_input", "answer_submitted")
INTEGRITY_LEVELS = ("high", "medium", "low")


class TaskTemplate(TimestampMixin, Base):
    """Шаблон задания из YAML (`app/skills/<spec>/templates/<node>/<L>_<id>.yaml`).

    `code` — id шаблона из YAML («be.D4.L2.composite_index»); `spec` — concept / invariant /
    vary / forbidden_changes / trap; в генерацию пула уходят только `status = reviewed`.
    """

    __tablename__ = "task_template"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    rubric: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )
    anchor_variant: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    difficulty_anchor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    stack_tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    max_time_sec: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("300"), default=300
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'"), default="draft"
    )

    variants: Mapped[list[TaskVariant]] = relationship(
        back_populates="template", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        sa.Index("ix_task_template_node_level", "node_id", "level"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        sa.CheckConstraint(
            "kind IN ('mcq_reasoned', 'code_review', 'short_design', 'scenario_personal')",
            name="kind_known",
        ),
        sa.CheckConstraint("status IN ('draft', 'reviewed', 'retired')", name="status_known"),
        sa.CheckConstraint("max_time_sec > 0", name="max_time_positive"),
    )


class TaskVariant(CreatedAtMixin, Base):
    """Сгенерированный и одобренный судьёй вариант; используется не более `max_uses` раз."""

    __tablename__ = "task_variant"

    id: Mapped[uuid.UUID] = uuid_pk()
    template_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("task_template.id", ondelete="CASCADE"), nullable=False
    )
    # {statement, code?, options?, answer_key?, reference_answer, trap_realized, ...}
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    judge_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    uses_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    max_uses: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3"), default=3
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'"), default="active"
    )
    retired_reason: Mapped[str | None] = mapped_column(sa.Text)
    # персональный сценарий из свершения кандидата — не попадает в общий пул
    personal_for_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE")
    )

    template: Mapped[TaskTemplate] = relationship(back_populates="variants")

    __table_args__ = (
        sa.UniqueConstraint("template_id", "content_hash", name="uq_task_variant_template_hash"),
        sa.Index("ix_task_variant_template_status", "template_id", "status"),
        sa.CheckConstraint("status IN ('active', 'retired', 'rejected')", name="status_known"),
        sa.CheckConstraint("uses_count >= 0 AND max_uses > 0", name="uses_range"),
    )


class TestSession(CreatedAtMixin, Base):
    """Сессия теста на грейд (PLAN.md §4.3): маршрут по графу, ранняя остановка, исход.

    `mode = seed` — сессия, созданная сидом/`bulk`-импортом с заданными `node_scores`:
    категория всё равно присваивается из сессии (инварианты §3.7 и §7.4 соблюдены).
    """

    __tablename__ = "test_session"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="RESTRICT"), nullable=False
    )
    target_grade: Mapped[str] = mapped_column(sa.Text, nullable=False)
    mode: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'full'"), default="full"
    )
    kind: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'initial'"), default="initial"
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'in_progress'"), default="in_progress"
    )
    # этап маршрута: base | links | personal | borderline | done
    stage: Mapped[str | None] = mapped_column(sa.Text)
    seed: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0
    )
    # {"be.B1": 0.85, ...} — итоговые баллы по узлам
    node_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    outcome: Mapped[str | None] = mapped_column(sa.Text)  # passed | failed | strong
    # служебные пометки: {"demo": bool, "draft_template_ids": [...], "warnings": [...]}
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    items: Mapped[list[TestItem]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TestItem.position",
    )

    __table_args__ = (
        sa.Index("ix_test_session_candidate_status", "candidate_id", "status"),
        sa.CheckConstraint("target_grade IN ('junior', 'middle', 'senior')", name="grade_known"),
        sa.CheckConstraint("mode IN ('full', 'demo', 'seed')", name="mode_known"),
        sa.CheckConstraint("kind IN ('initial', 'retest_up', 'retest_down')", name="kind_known"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'finished', 'expired', 'abandoned')", name="status_known"
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('passed', 'failed', 'strong')", name="outcome_known"
        ),
    )


class TestItem(Base):
    """Выданное задание сессии; `candidate_id` денормализован ради UNIQUE (candidate, variant)."""

    __tablename__ = "test_item"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("test_session.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("task_variant.id", ondelete="RESTRICT"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="RESTRICT"), nullable=False
    )
    level: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    position: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    served_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    deadline_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    grading: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # late | timeout | personal | second_attempt | grading_pending | ...
    flags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )

    session: Mapped[TestSession] = relationship(back_populates="items")

    __table_args__ = (
        sa.UniqueConstraint("candidate_id", "variant_id", name="uq_test_item_candidate_variant"),
        sa.UniqueConstraint("session_id", "position", name="uq_test_item_session_position"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
    )


class TestSessionEvent(Base):
    """Событие телеметрии раннера теста (сигналы целостности, ПДн-минимизация).

    В `payload` попадают ТОЛЬКО длины и тайминги (`item_id`, `len`, `duration_ms`,
    `chars_typed`, `chars_pasted`) — тексты ответов не сохраняются принципиально.
    `client_ts` — время на клиенте (для пар blur/focus и длительностей),
    `received_at` — серверное время приёма.
    """

    __tablename__ = "test_session_event"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("test_session.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    client_ts: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.Index("ix_test_session_event_session_ts", "session_id", "client_ts"),
        sa.CheckConstraint(
            "kind IN ('paste', 'blur', 'focus', 'item_shown', 'first_input', 'answer_submitted')",
            name="kind_known",
        ),
    )


class TestSessionIntegrity(Base):
    """Кэш агрегата целостности сессии (пересчитывается из `test_session_event`).

    Строка существует только у сессий, по которым была телеметрия: отсутствие строки
    в выдаче работодателя означает `integrity_level = null` («данных нет»).
    `candidate_id` денормализован для выборки «последняя завершённая сессия кандидата».
    """

    __tablename__ = "test_session_integrity"

    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("test_session.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # [{code, title, count, detail}] — состав флагов на момент пересчёта
    flags: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )
    events_total: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (sa.CheckConstraint("level IN ('high', 'medium', 'low')", name="level_known"),)


class GradeHistory(CreatedAtMixin, Base):
    """История присвоений и попыток смены грейда (страница «Категория и грейд»)."""

    __tablename__ = "grade_history"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("test_session.id", ondelete="SET NULL")
    )
    from_grade: Mapped[str | None] = mapped_column(sa.Text)
    to_grade: Mapped[str | None] = mapped_column(sa.Text)
    outcome: Mapped[str | None] = mapped_column(sa.Text)  # passed | failed | strong
    reason: Mapped[str | None] = mapped_column(sa.Text)


__all__ = [
    "EVENT_KINDS",
    "INTEGRITY_LEVELS",
    "SESSION_KINDS",
    "SESSION_MODES",
    "SESSION_OUTCOMES",
    "SESSION_STATUSES",
    "TEMPLATE_KINDS",
    "TEMPLATE_STATUSES",
    "VARIANT_STATUSES",
    "GradeHistory",
    "TaskTemplate",
    "TaskVariant",
    "TestItem",
    "TestSession",
    "TestSessionEvent",
    "TestSessionIntegrity",
]
