"""Зал историй 3.4 (#32): открытый набор за столы — recruiting + ёмкость seats_total.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19 07:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_sessions",
        sa.Column("recruiting", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("game_sessions", sa.Column("seats_total", sa.Integer(), nullable=True))
    op.create_index("ix_game_sessions_status_recruiting", "game_sessions", ["status", "recruiting"])


def downgrade() -> None:
    op.drop_index("ix_game_sessions_status_recruiting", table_name="game_sessions")
    op.drop_column("game_sessions", "seats_total")
    op.drop_column("game_sessions", "recruiting")
