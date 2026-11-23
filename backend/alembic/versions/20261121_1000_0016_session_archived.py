"""Безопасное удаление стола: game_sessions.archived (скрыт из списков, запись остаётся).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-21 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_sessions",
        sa.Column("archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "archived")
