"""Сигналы целостности тестовой сессии (телеметрия раннера, PLAN.md §4):

- `test_session_event` — батчи телеметрии от раннера теста: только виды событий,
  тайминги и длины (`item_id`, `len`, `duration_ms`, `chars_typed`, `chars_pasted`);
  тексты ответов в события не попадают (ПДн-минимизация);
- `test_session_integrity` — кэш агрегата: уровень high/medium/low, флаги и счётчик
  событий; строка есть только у сессий с телеметрией (нет строки = «данных нет»).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_session_event",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("client_ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('paste', 'blur', 'focus', 'item_shown', 'first_input', "
            "'answer_submitted')",
            name=op.f("ck_test_session_event_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["test_session.id"],
            name=op.f("fk_test_session_event_session_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_session_event")),
    )
    op.create_index(
        "ix_test_session_event_session_ts", "test_session_event", ["session_id", "client_ts"]
    )

    op.create_table(
        "test_session_integrity",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("flags", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("events_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('high', 'medium', 'low')",
            name=op.f("ck_test_session_integrity_level_known"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["test_session.id"],
            name=op.f("fk_test_session_integrity_session_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate_profile.id"],
            name=op.f("fk_test_session_integrity_candidate_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_test_session_integrity")),
    )
    op.create_index(
        op.f("ix_test_session_integrity_candidate_id"), "test_session_integrity", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_table("test_session_integrity")
    op.drop_table("test_session_event")
