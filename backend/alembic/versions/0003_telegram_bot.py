"""Телеграм-бот «ФСП.Орбита» (PLAN.md §5.6 — доп. канал, §7.4 — расширение):

- `tg_link` — привязка Telegram-чата к пользователю платформы или к участнику компании
  без аккаунта (HR, приглашённый по одноразовому токену, без входа на платформу);
- `tg_link_token` — одноразовые коды привязки (в БД — HMAC кода, как invite-токены);
- `tg_outbox` — журнал исходящих сообщений бота: в stub-режиме — единственная
  «отправка», в polling — журнал + дедупликация пушей по (kind, ref_id, chat).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tg_link",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("member_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('candidate', 'employer')", name=op.f("ck_tg_link_role_known")
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR member_id IS NOT NULL",
            name=op.f("ck_tg_link_subject_present"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_tg_link_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["company_member.id"],
            name=op.f("fk_tg_link_member_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tg_link")),
        sa.UniqueConstraint("tg_chat_id", name=op.f("uq_tg_link_tg_chat_id")),
    )
    op.create_index(op.f("ix_tg_link_user_id"), "tg_link", ["user_id"])
    op.create_index(op.f("ix_tg_link_member_id"), "tg_link", ["member_id"])

    op.create_table(
        "tg_link_token",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('candidate', 'employer')", name=op.f("ck_tg_link_token_role_known")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_tg_link_token_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tg_link_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_tg_link_token_token_hash")),
    )
    op.create_index(op.f("ix_tg_link_token_user_id"), "tg_link_token", ["user_id"])

    op.create_table(
        "tg_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("buttons", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'stub'"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('stub', 'sent', 'failed')", name=op.f("ck_tg_outbox_status_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tg_outbox")),
    )
    op.create_index("ix_tg_outbox_chat_kind", "tg_outbox", ["tg_chat_id", "kind"])
    op.create_index("ix_tg_outbox_kind_ref", "tg_outbox", ["kind", "ref_id"])


def downgrade() -> None:
    op.drop_table("tg_outbox")
    op.drop_table("tg_link_token")
    op.drop_table("tg_link")
