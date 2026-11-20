"""scenario_chunks: фрагменты сценария для гибридного поиска (подэтап 2.2)

Вектор pgvector и генерируемый tsvector по словарю языка фрагмента, индексы hnsw и gin.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18 17:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TSV = (
    "to_tsvector(CASE WHEN language = 'ru' THEN 'russian'::regconfig "
    "ELSE 'english'::regconfig END, text)"
)


def upgrade() -> None:
    op.create_table(
        "scenario_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("scene_id", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(_TSV, persisted=True),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["scenario_versions.id"],
            name=op.f("fk_scenario_chunks_version_id_scenario_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["scenario_entities.id"],
            name=op.f("fk_scenario_chunks_entity_id_scenario_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_chunks")),
        sa.UniqueConstraint(
            "version_id", "entity_id", "ord", name=op.f("uq_scenario_chunks_version_id")
        ),
    )
    op.create_index(op.f("ix_scenario_chunks_version_id"), "scenario_chunks", ["version_id"])
    op.create_index(op.f("ix_scenario_chunks_entity_id"), "scenario_chunks", ["entity_id"])
    op.create_index(
        "ix_scenario_chunks_embedding",
        "scenario_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_scenario_chunks_tsv", "scenario_chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_scenario_chunks_tsv", table_name="scenario_chunks")
    op.drop_index("ix_scenario_chunks_embedding", table_name="scenario_chunks")
    op.drop_index(op.f("ix_scenario_chunks_entity_id"), table_name="scenario_chunks")
    op.drop_index(op.f("ix_scenario_chunks_version_id"), table_name="scenario_chunks")
    op.drop_table("scenario_chunks")
