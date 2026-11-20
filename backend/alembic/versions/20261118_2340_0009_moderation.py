"""Модерация стола лидером команды: мут места и запрет повторного входа.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18 23:40:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "seats",
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "session_kicks",
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("session_kicks")
    op.drop_column("seats", "muted")
