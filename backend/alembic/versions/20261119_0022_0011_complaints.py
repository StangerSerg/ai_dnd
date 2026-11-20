"""Админка 3.9: очередь жалоб игроков (надзор и модерация, задача #28).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19 00:22:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "complaints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reporter_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "resolved_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(500), nullable=True),
    )
    op.create_index("ix_complaints_session_id", "complaints", ["session_id"])
    op.create_index("ix_complaints_reporter_id", "complaints", ["reporter_id"])
    op.create_index("ix_complaints_status_created", "complaints", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_complaints_status_created", table_name="complaints")
    op.drop_index("ix_complaints_reporter_id", table_name="complaints")
    op.drop_index("ix_complaints_session_id", table_name="complaints")
    op.drop_table("complaints")
