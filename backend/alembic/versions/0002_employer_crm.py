"""Профили работодателя и мини-CRM (PLAN.md §5.6, §7.4 — расширение):

- `company.kind` — личный (`personal`, создаётся при первом входе работодателя)
  или корпоративный (`corporate`) профиль;
- `company_member` — участники компании (owner | hr | hiring_manager); `user_id` NULLABLE
  (эйчар может быть приглашён без входа на платформу), ПДн приглашённого — `*_enc` (bytea)
  + blind index, одноразовый invite-токен хранится только HMAC-хэшем;
- `need_pipeline_entry` — мини-CRM: стадия воронки на пару «потребность × кандидат»
  (shortlist → invited → accepted → interview → offer → hired | declined);
- `pipeline_stage_event` — append-only история переходов стадий для аудита.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company",
        sa.Column("kind", sa.Text(), server_default=sa.text("'personal'"), nullable=False),
    )
    op.create_check_constraint(
        "ck_company_kind_known", "company", "kind IN ('personal', 'corporate')"
    )

    op.create_table(
        "company_member",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'invited'"), nullable=False),
        sa.Column("full_name_enc", sa.LargeBinary(), nullable=True),
        sa.Column("full_name_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("email_enc", sa.LargeBinary(), nullable=True),
        sa.Column("email_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("telegram_enc", sa.LargeBinary(), nullable=True),
        sa.Column("telegram_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("invite_channel", sa.Text(), nullable=True),
        sa.Column("invite_token_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("invited_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "invite_channel IS NULL OR invite_channel IN ('email', 'telegram')",
            name=op.f("ck_company_member_channel_known"),
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'hr', 'hiring_manager')",
            name=op.f("ck_company_member_role_known"),
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'revoked')",
            name=op.f("ck_company_member_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name=op.f("fk_company_member_company_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["app_user.id"],
            name=op.f("fk_company_member_invited_by"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_company_member_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_member")),
        sa.UniqueConstraint("invite_token_hash", name=op.f("uq_company_member_invite_token_hash")),
    )
    op.create_index(
        op.f("ix_company_member_company_id"), "company_member", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_company_member_email_hash"), "company_member", ["email_hash"], unique=False
    )
    op.create_index(
        op.f("ix_company_member_telegram_hash"), "company_member", ["telegram_hash"], unique=False
    )
    op.create_index(
        "uq_company_member_company_user",
        "company_member",
        ["company_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.create_table(
        "need_pipeline_entry",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("need_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.Text(), server_default=sa.text("'shortlist'"), nullable=False),
        sa.Column("invitation_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('shortlist', 'invited', 'accepted', 'interview', "
            "'offer', 'hired', 'declined')",
            name=op.f("ck_need_pipeline_entry_stage_known"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate_profile.id"],
            name=op.f("fk_need_pipeline_entry_candidate_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitation.id"],
            name=op.f("fk_need_pipeline_entry_invitation_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["need_id"],
            ["employer_need.id"],
            name=op.f("fk_need_pipeline_entry_need_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_need_pipeline_entry")),
        sa.UniqueConstraint("need_id", "candidate_id", name="uq_need_pipeline_entry_pair"),
    )
    op.create_index(
        "ix_need_pipeline_entry_need_stage",
        "need_pipeline_entry",
        ["need_id", "stage"],
        unique=False,
    )

    op.create_table(
        "pipeline_stage_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("from_stage", sa.Text(), nullable=True),
        sa.Column("to_stage", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_user.id"],
            name=op.f("fk_pipeline_stage_event_actor_user_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["need_pipeline_entry.id"],
            name=op.f("fk_pipeline_stage_event_entry_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_stage_event")),
    )
    op.create_index(
        op.f("ix_pipeline_stage_event_entry_id"), "pipeline_stage_event", ["entry_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pipeline_stage_event_entry_id"), table_name="pipeline_stage_event")
    op.drop_table("pipeline_stage_event")
    op.drop_index("ix_need_pipeline_entry_need_stage", table_name="need_pipeline_entry")
    op.drop_table("need_pipeline_entry")
    op.drop_index(
        "uq_company_member_company_user",
        table_name="company_member",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_company_member_telegram_hash"), table_name="company_member")
    op.drop_index(op.f("ix_company_member_email_hash"), table_name="company_member")
    op.drop_index(op.f("ix_company_member_company_id"), table_name="company_member")
    op.drop_table("company_member")
    op.drop_constraint("ck_company_kind_known", "company", type_="check")
    op.drop_column("company", "kind")
