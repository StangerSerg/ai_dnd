"""Места за столом: seats, seat_reads; host_id и invite_token у game_sessions (подэтап 3.3).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_sessions", sa.Column("host_id", sa.Uuid(), nullable=True))
    op.add_column("game_sessions", sa.Column("invite_token", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        op.f("fk_game_sessions_host_id_users"), "game_sessions", "users", ["host_id"], ["id"]
    )
    op.create_unique_constraint(
        op.f("uq_game_sessions_invite_token"), "game_sessions", ["invite_token"]
    )
    op.create_index(op.f("ix_game_sessions_host_id"), "game_sessions", ["host_id"], unique=False)
    op.create_table(
        "seats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("controller", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("ai_persona", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seats")),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_seats_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_seats_user_id_users")),
        sa.ForeignKeyConstraint(
            ["character_id"], ["characters.id"], name=op.f("fk_seats_character_id_characters")
        ),
        sa.UniqueConstraint("session_id", "actor_id", name=op.f("uq_seats_session_id")),
    )
    op.create_index(op.f("ix_seats_session_id"), "seats", ["session_id"], unique=False)
    op.create_table(
        "seat_reads",
        sa.Column("seat_id", sa.Uuid(), nullable=False),
        sa.Column("last_seen_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("seat_id", name=op.f("pk_seat_reads")),
        sa.ForeignKeyConstraint(
            ["seat_id"], ["seats.id"], name=op.f("fk_seat_reads_seat_id_seats"), ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("seat_reads")
    op.drop_index(op.f("ix_seats_session_id"), table_name="seats")
    op.drop_table("seats")
    op.drop_index(op.f("ix_game_sessions_host_id"), table_name="game_sessions")
    op.drop_constraint(op.f("uq_game_sessions_invite_token"), "game_sessions", type_="unique")
    op.drop_constraint(op.f("fk_game_sessions_host_id_users"), "game_sessions", type_="foreignkey")
    op.drop_column("game_sessions", "invite_token")
    op.drop_column("game_sessions", "host_id")
