"""`llm_call` — журнал вызовов LLM (PLAN.md §6): стоимость, латентность, кэш, ошибки.

В `request_json` / `response_json` ПДн не попадают: вход проходит `sanitize_for_llm()`,
идентификаторы — только UUID. Тест `test_no_pii_in_llm_calls` сканирует обе колонки.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, uuid_pk

LLM_CALL_STATUSES = ("ok", "error", "cached", "fallback")


class LlmCall(CreatedAtMixin, Base):
    __tablename__ = "llm_call"

    id: Mapped[uuid.UUID] = uuid_pk()
    # need_parse | variant_generate | variant_judge | answer_grade | ... (имя промпта)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    # stub | gigachat | yandex | replay | rules
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # sha256(kind + prompt_version + model + вход) — ключ кэша ответов
    prompt_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_est: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4))
    # ok | error | cached | fallback
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.CheckConstraint("status IN ('ok', 'error', 'cached', 'fallback')", name="status_known"),
        sa.Index("ix_llm_call_created_at", "created_at"),
    )
