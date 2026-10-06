"""Справочник навыков (PLAN.md §3.2, §3.5, §7.4): отрасли, специализации, узлы графа,
рёбра, профили грейдов и маппинг дисциплин ФСП в узлы.

Источник данных — YAML в `app/skills/` (`<spec>/graph.yaml`, `grade_profiles.yaml`,
`fsp_mapping.yaml`); сид модуля `skills` грузит их сюда идемпотентно по натуральным ключам.
Идентификатор узла — строка вида «be.D1» (специализация.код), слой — D (глубина),
B (база) или C (контекст). Рёбра между специализациями имеют вид `bridge`.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, uuid_pk

SPECIALIZATION_STATUSES = ("active", "preview")
LAYERS = ("D", "B", "C")
EDGE_KINDS = ("vertical", "lateral", "bridge")
GRADES = ("junior", "middle", "senior")
TASK_LEVELS = (1, 2, 3)


class Industry(Base):
    """Короткий справочник отраслей: влияет только на отображение и фильтр."""

    __tablename__ = "industry"

    code: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100"), default=100
    )


class Specialization(TimestampMixin, Base):
    """Специализация = граф из 13 узлов; `preview` — только граф, без теста и банка."""

    __tablename__ = "specialization"

    code: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # be | fe | data | qa | devops
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'preview'"), default="preview"
    )
    graph_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100"), default=100
    )

    nodes: Mapped[list[SkillNode]] = relationship(
        back_populates="specialization", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (sa.CheckConstraint("status IN ('active', 'preview')", name="status_known"),)


class SkillNode(Base):
    """Узел графа: «be.D1», слой D|B|C, название и описание для рубрик и объяснений."""

    __tablename__ = "skill_node"

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # «be.D1»
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)  # «D1»
    layer: Mapped[str] = mapped_column(sa.CHAR(1), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100"), default=100
    )

    specialization: Mapped[Specialization] = relationship(back_populates="nodes")

    __table_args__ = (
        sa.UniqueConstraint("specialization_code", "code", name="uq_skill_node_spec_code"),
        sa.CheckConstraint("layer IN ('D', 'B', 'C')", name="layer_known"),
    )


class SkillEdge(Base):
    """Ребро графа; все рёбра «толстые», вид нужен формам профиля и объяснениям."""

    __tablename__ = "skill_edge"

    source_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[str] = mapped_column(
        sa.ForeignKey("skill_node.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # vertical | lateral | bridge

    __table_args__ = (
        sa.CheckConstraint("kind IN ('vertical', 'lateral', 'bridge')", name="kind_known"),
        sa.CheckConstraint("source_id <> target_id", name="no_self_loop"),
        sa.Index("ix_skill_edge_target_id", "target_id"),
    )


class GradeProfile(Base):
    """Профиль грейда (PLAN.md §3.4): целевой подграф T, уровень заданий, пороги."""

    __tablename__ = "grade_profile"

    id: Mapped[uuid.UUID] = uuid_pk()
    specialization_code: Mapped[str] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="CASCADE"), nullable=False
    )
    grade: Mapped[str] = mapped_column(sa.Text, nullable=False)  # junior | middle | senior
    target_nodes: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    task_level: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    min_active: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    min_q: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    # дополнительные условия: {"outside_layer": "B"} / {"layers_required": ["D", "C"]}
    constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    # бюджет заданий сессии: {"min_items": 6, "max_items": 12}
    budget: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    # укороченный профиль для демо-режима (TEST_DEMO_MODE)
    demo: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.UniqueConstraint("specialization_code", "grade", name="uq_grade_profile_spec_grade"),
        sa.CheckConstraint("grade IN ('junior', 'middle', 'senior')", name="grade_known"),
        sa.CheckConstraint("task_level BETWEEN 1 AND 3", name="task_level_range"),
    )


class FspDisciplineMap(CreatedAtMixin, Base):
    """Статический маппинг «дисциплина × тип события ФСП → узлы и уровень» (PLAN.md §3.5).

    `node_weights` — список {"node_id": "be.D2", "weight": 1.0};
    `level_rule` — правило уровня: {"by": "percentile", "thresholds": {...}} или
    {"by": "tier", "map": {"winner": 2, "participant": 1}}.
    """

    __tablename__ = "fsp_discipline_map"

    id: Mapped[uuid.UUID] = uuid_pk()
    discipline: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # тип события; «*» — любой
    event_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'*'"), default="*"
    )
    specialization_code: Mapped[str | None] = mapped_column(
        sa.ForeignKey("specialization.code", ondelete="CASCADE")
    )
    node_weights: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    level_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.UniqueConstraint("discipline", "event_type", name="uq_fsp_discipline_map_discipline"),
    )


__all__ = [
    "EDGE_KINDS",
    "GRADES",
    "LAYERS",
    "SPECIALIZATION_STATUSES",
    "TASK_LEVELS",
    "FspDisciplineMap",
    "GradeProfile",
    "Industry",
    "SkillEdge",
    "SkillNode",
    "Specialization",
]
