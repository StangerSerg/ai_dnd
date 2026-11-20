"""Админка 3.9: бан пользователей, журнал аудита, переопределения лимитов.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18 23:55:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("details", JSONB(), nullable=False),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_table(
        "spend_limits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("daily_credits", sa.Integer(), nullable=True),
        sa.Column("daily_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("monthly_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("scope", "scope_id"),
    )


def downgrade() -> None:
    op.drop_table("spend_limits")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("users", "banned_at")
