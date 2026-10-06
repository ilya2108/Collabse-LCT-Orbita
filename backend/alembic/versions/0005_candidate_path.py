"""Путь кандидата до категории (PLAN.md §3.4, §4.3; контракт интегратора 11.10):

- `candidate_profile.specialization_code` — специализация из анкеты (ФСП id данные пока
  не отдаёт, кандидат заполняет вручную); без неё сессия теста не создаётся (409);
- `test_session.details` — служебные пометки сессии: демо-режим, шаблоны в статусе draft,
  предупреждения подбора заданий ({"demo": bool, "draft_template_ids": [], "warnings": []}).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_profile", sa.Column("specialization_code", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_candidate_profile_specialization_code"),
        "candidate_profile",
        "specialization",
        ["specialization_code"],
        ["code"],
        ondelete="SET NULL",
    )
    op.add_column(
        "test_session",
        sa.Column("details", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("test_session", "details")
    op.drop_constraint(
        op.f("fk_candidate_profile_specialization_code"), "candidate_profile", type_="foreignkey"
    )
    op.drop_column("candidate_profile", "specialization_code")
