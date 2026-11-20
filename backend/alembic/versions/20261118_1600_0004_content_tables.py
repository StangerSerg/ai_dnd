"""Таблицы контента: rulesets, sheet_templates, scenarios, scenario_versions,
scenario_entities, characters; привязка game_sessions к версии (подэтап 2.1).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rulesets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("forked_from", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rulesets")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_rulesets_name")),
    )
    op.create_index(op.f("ix_rulesets_author_id"), "rulesets", ["author_id"], unique=False)
    op.create_table(
        "sheet_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("forked_from", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_templates")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_sheet_templates_name")),
    )
    op.create_index(
        op.f("ix_sheet_templates_author_id"), "sheet_templates", ["author_id"], unique=False
    )
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("forked_from", sa.Uuid(), nullable=True),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
        sa.UniqueConstraint("slug", "language", name=op.f("uq_scenarios_slug")),
    )
    op.create_index(op.f("ix_scenarios_author_id"), "scenarios", ["author_id"], unique=False)
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("ruleset_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_template_id", sa.Uuid(), nullable=False),
        sa.Column("info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("map", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_versions")),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_scenario_versions_scenario_id_scenarios"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ruleset_id"], ["rulesets.id"], name=op.f("fk_scenario_versions_ruleset_id_rulesets")
        ),
        sa.ForeignKeyConstraint(
            ["sheet_template_id"],
            ["sheet_templates.id"],
            name=op.f("fk_scenario_versions_sheet_template_id_sheet_templates"),
        ),
        sa.UniqueConstraint("scenario_id", "number", name=op.f("uq_scenario_versions_scenario_id")),
    )
    op.create_index(
        op.f("ix_scenario_versions_scenario_id"), "scenario_versions", ["scenario_id"], unique=False
    )
    op.create_table(
        "scenario_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_entities")),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["scenario_versions.id"],
            name=op.f("fk_scenario_entities_version_id_scenario_versions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "version_id", "type", "key", name=op.f("uq_scenario_entities_version_id")
        ),
    )
    op.create_index(
        op.f("ix_scenario_entities_version_id"), "scenario_entities", ["version_id"], unique=False
    )
    op.create_table(
        "characters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portrait", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_characters")),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["sheet_templates.id"],
            name=op.f("fk_characters_template_id_sheet_templates"),
        ),
    )
    op.create_index(op.f("ix_characters_owner_id"), "characters", ["owner_id"], unique=False)
    op.add_column("game_sessions", sa.Column("scenario_version_id", sa.Uuid(), nullable=True))
    op.add_column("game_sessions", sa.Column("ruleset_id", sa.Uuid(), nullable=True))
    op.add_column("game_sessions", sa.Column("sheet_template_id", sa.Uuid(), nullable=True))
    op.add_column(
        "game_sessions",
        sa.Column("state_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("game_sessions", sa.Column("summary", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_game_sessions_scenario_version_id_scenario_versions"),
        "game_sessions",
        "scenario_versions",
        ["scenario_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_game_sessions_ruleset_id_rulesets"),
        "game_sessions",
        "rulesets",
        ["ruleset_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_game_sessions_sheet_template_id_sheet_templates"),
        "game_sessions",
        "sheet_templates",
        ["sheet_template_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_game_sessions_scenario_version_id"),
        "game_sessions",
        ["scenario_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_game_sessions_scenario_version_id"), table_name="game_sessions")
    op.drop_constraint(
        op.f("fk_game_sessions_sheet_template_id_sheet_templates"),
        "game_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_game_sessions_ruleset_id_rulesets"), "game_sessions", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_game_sessions_scenario_version_id_scenario_versions"),
        "game_sessions",
        type_="foreignkey",
    )
    op.drop_column("game_sessions", "summary")
    op.drop_column("game_sessions", "state_version")
    op.drop_column("game_sessions", "sheet_template_id")
    op.drop_column("game_sessions", "ruleset_id")
    op.drop_column("game_sessions", "scenario_version_id")
    op.drop_index(op.f("ix_characters_owner_id"), table_name="characters")
    op.drop_table("characters")
    op.drop_index(op.f("ix_scenario_entities_version_id"), table_name="scenario_entities")
    op.drop_table("scenario_entities")
    op.drop_index(op.f("ix_scenario_versions_scenario_id"), table_name="scenario_versions")
    op.drop_table("scenario_versions")
    op.drop_index(op.f("ix_scenarios_author_id"), table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index(op.f("ix_sheet_templates_author_id"), table_name="sheet_templates")
    op.drop_table("sheet_templates")
    op.drop_index(op.f("ix_rulesets_author_id"), table_name="rulesets")
    op.drop_table("rulesets")
