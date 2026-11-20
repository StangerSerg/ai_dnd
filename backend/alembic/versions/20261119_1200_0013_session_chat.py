"""Чат вне игры 3.4: OOC-сообщения участников стола отдельно от журнала игры.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_session_chat_messages_session_id", "session_chat_messages", ["session_id"])
    op.create_index(
        "ix_session_chat_session_created", "session_chat_messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_session_chat_session_created", table_name="session_chat_messages")
    op.drop_index("ix_session_chat_messages_session_id", table_name="session_chat_messages")
    op.drop_table("session_chat_messages")
