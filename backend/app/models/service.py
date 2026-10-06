"""Служебные сущности (PLAN.md §7.4): прогоны валидации; журнал LLM и аудит
живут в `models/llm.py` и `models/audit.py` (каркас) и реэкспортируются отсюда.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.audit import AuditLog
from app.models.base import Base, CreatedAtMixin, uuid_pk
from app.models.llm import LlmCall


class ValidationRun(CreatedAtMixin, Base):
    """Прогон `make validate` (PLAN.md §8): ярус, параметры, метрики, путь к отчёту."""

    __tablename__ = "validation_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    # units | personas_stat | personas_bank | variants | judge | panel | matching | contact | perf
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    report_path: Mapped[str | None] = mapped_column(sa.Text)
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


__all__ = ["AuditLog", "LlmCall", "ValidationRun"]
