"""credit_ledger: журнал кредитов, баланс = sum(delta) (подэтап 3.6)

Записи только добавляются. FK на users: таблицу создаёт миграция 0006 параллельной
задачи; цепочку миграций собирает Root, upgrade здесь не запускается.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18 22:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("ref_type", sa.String(length=40), nullable=True),
        sa.Column("ref_id", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_credit_ledger_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_ledger")),
    )
    op.create_index(
        "ix_credit_ledger_user_id_created_at", "credit_ledger", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_user_id_created_at", table_name="credit_ledger")
    op.drop_table("credit_ledger")
