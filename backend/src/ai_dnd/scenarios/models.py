"""Таблицы сценариев: scenarios, scenario_versions, scenario_entities (архитектура, 8/13).

Сценарий — набор сущностей. Опубликованная версия неизменяема; сессия привязана к
версии, поэтому правки автора не ломают идущую игру. Видимость сценария
(private/unlisted/public) — на уровне данных, видимость сущности (public/dm) — отдельно.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base

# Размерность вектора фиксирована миграцией; смена модели с другой размерностью
# требует новой миграции и переиндексации (архитектура 8.4).
EMBEDDING_DIM = 384


class ScenarioRow(Base):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("slug", "language"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8))
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    tags: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    forked_from: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Без FK: scenarios ↔ scenario_versions цикличны; версия проставляется после публикации.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScenarioVersionRow(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("scenario_id", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    ruleset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rulesets.id"))
    sheet_template_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sheet_templates.id"))
    # ScenarioInfo: title, language, ruleset/sheet refs, start_scene, synopsis, tone.
    info: Mapped[dict[str, Any]] = mapped_column(JSONB)
    map: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScenarioEntityRow(Base):
    __tablename__ = "scenario_entities"
    __table_args__ = (UniqueConstraint("version_id", "type", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(String(16), default="public")


class ScenarioChunkRow(Base):
    """Фрагмент сущности для поиска: вектор и полнотекстовый индекс (архитектура 8.4, 13).

    Видимость — на уровне текста фрагмента: ``read_aloud`` сцены публичный, ``dm_notes``
    только для ведущего. Фильтр видимости стоит в SQL (архитектура 9.2). ``tsv`` —
    генерируемый столбец по словарю языка фрагмента (russian/english).
    """

    __tablename__ = "scenario_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "entity_id", "ord"),
        Index(
            "ix_scenario_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_scenario_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scenario_entities.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(120))
    scene_id: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(8))
    visibility: Mapped[str] = mapped_column(String(16), default="public")
    ord: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector(CASE WHEN language = 'ru' THEN 'russian'::regconfig "
            "ELSE 'english'::regconfig END, text)",
            persisted=True,
        ),
    )
    # Имя модели эмбеддингов: сменили модель — переиндексировали.
    embedding_model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
