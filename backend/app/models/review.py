"""Инструмент проверки жюри (PLAN.md §7.4, §7.5) — изолирован от продуктовых сущностей.

`pair_run` — прогон набора пар «текст вакансии + текст резюме» (`make import-pairs`,
`POST /internal/pairs/run`); `imported_candidate` — запись из резюме без `app_user`,
`candidate_profile`, подтверждений и категории: не участвует в банке, обычной подборке
и приглашениях по построению (FK приглашения — только на `candidate_profile`).
Удаляется `POST /internal/demo/reset`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, uuid_pk

PAIR_RUN_MODES = ("unverified", "bulk")


class PairRun(CreatedAtMixin, Base):
    __tablename__ = "pair_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_file: Mapped[str | None] = mapped_column(sa.Text)
    mode: Mapped[str] = mapped_column(sa.Text, nullable=False)  # unverified | bulk
    # отчёт: P@k, сравнение путей, список потребностей и версий
    report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    candidates: Mapped[list[ImportedCandidate]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (sa.CheckConstraint("mode IN ('unverified', 'bulk')", name="mode_known"),)


class ImportedCandidate(CreatedAtMixin, Base):
    """Запись из резюме: факты без ПДн, заявленные узлы [{id, level}], стек."""

    __tablename__ = "imported_candidate"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("pair_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_ref: Mapped[str | None] = mapped_column(sa.Text)
    resume_facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    claimed_nodes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), default=list
    )
    stack_tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )

    run: Mapped[PairRun] = relationship(back_populates="candidates")


__all__ = ["PAIR_RUN_MODES", "ImportedCandidate", "PairRun"]
