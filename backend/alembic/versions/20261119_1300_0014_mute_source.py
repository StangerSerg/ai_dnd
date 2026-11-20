"""Нокаут переиспользует мут (решение 19.11): источник мута места leader/knockout.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19 13:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("seats", sa.Column("mute_source", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("seats", "mute_source")
