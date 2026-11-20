"""Индексация опубликованной версии сценария (архитектура 8.4).

Сущности нарезаются на фрагменты, считаются эмбеддинги, всё пишется в scenario_chunks.
При новой версии пересчитываются только изменённые сущности: у неизменённых фрагменты
и векторы копируются из предыдущей версии. Смена модели эмбеддингов требует переиндексации.

Тяжёлая работа (эмбеддинги) выполняется в воркере; функция общая для воркера и консоли.
"""

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.rag.chunking import Chunk, chunk_entity
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.scenarios.models import (
    EMBEDDING_DIM,
    ScenarioChunkRow,
    ScenarioEntityRow,
    ScenarioVersionRow,
)

Sessions = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class IndexReport:
    entities: int
    chunks: int
    reused: int
    embedded: int


async def index_version(
    sessions: Sessions,
    version_id: uuid.UUID,
    *,
    embedder: Embedder,
    previous_version_id: uuid.UUID | None = None,
) -> IndexReport:
    """Нарезать и проиндексировать версию. Идемпотентно: перезапись заменяет фрагменты."""
    if embedder.dim != EMBEDDING_DIM:
        raise ValueError(
            f"Размерность модели {embedder.dim} не совпадает с индексом {EMBEDDING_DIM}"
        )

    async with sessions() as db:
        version = await db.get(ScenarioVersionRow, version_id)
        if version is None:
            raise LookupError(f"Нет версии сценария {version_id}")
        language = str(version.info.get("language", "ru"))
        entities = list(
            await db.scalars(
                select(ScenarioEntityRow)
                .where(ScenarioEntityRow.version_id == version_id)
                .order_by(ScenarioEntityRow.type, ScenarioEntityRow.key)
            )
        )
        prev_entities, prev_chunks = await _previous(db, previous_version_id, embedder.model_name)

        reused: list[ScenarioChunkRow] = []
        to_embed: list[tuple[ScenarioEntityRow, Chunk]] = []
        for entity in entities:
            prev = prev_entities.get((entity.type, entity.key))
            prev_chunks_for = prev_chunks.get(prev.id, []) if prev is not None else []
            # Переиспользуем только при наличии совместимых фрагментов: если предыдущая
            # версия без индекса или с другой моделью, равные данные всё равно эмбеддим.
            if prev is not None and prev.data == entity.data and prev_chunks_for:
                for prev_chunk in prev_chunks_for:
                    reused.append(_copy_chunk(prev_chunk, version_id, entity.id))
                continue
            for chunk in chunk_entity(entity.type, entity.key, entity.data, language):
                to_embed.append((entity, chunk))

        # Эмбеддинги — CPU-работа в потоке, чтобы не блокировать event loop воркера
        # (иначе рвётся соединение с Redis по таймауту).
        vectors = await asyncio.to_thread(embedder.embed, [chunk.text for _, chunk in to_embed])
        embedded = [
            _new_chunk(entity, chunk, vector, embedder.model_name, version_id)
            for (entity, chunk), vector in zip(to_embed, vectors, strict=True)
        ]

        await db.execute(delete(ScenarioChunkRow).where(ScenarioChunkRow.version_id == version_id))
        db.add_all(reused + embedded)
        await db.commit()

    return IndexReport(
        entities=len(entities),
        chunks=len(reused) + len(embedded),
        reused=len(reused),
        embedded=len(embedded),
    )


async def _previous(
    db: AsyncSession, previous_version_id: uuid.UUID | None, model_name: str
) -> tuple[dict[tuple[str, str], ScenarioEntityRow], dict[uuid.UUID, list[ScenarioChunkRow]]]:
    """Сущности и фрагменты предыдущей версии — только если модель та же."""
    if previous_version_id is None:
        return {}, {}
    entities = {
        (e.type, e.key): e
        for e in await db.scalars(
            select(ScenarioEntityRow).where(ScenarioEntityRow.version_id == previous_version_id)
        )
    }
    chunks: dict[uuid.UUID, list[ScenarioChunkRow]] = {}
    rows = await db.scalars(
        select(ScenarioChunkRow).where(
            ScenarioChunkRow.version_id == previous_version_id,
            ScenarioChunkRow.embedding_model == model_name,
        )
    )
    for row in rows:
        chunks.setdefault(row.entity_id, []).append(row)
    return entities, chunks


def _copy_chunk(
    chunk: ScenarioChunkRow, version_id: uuid.UUID, entity_id: uuid.UUID
) -> ScenarioChunkRow:
    return ScenarioChunkRow(
        version_id=version_id,
        entity_id=entity_id,
        type=chunk.type,
        key=chunk.key,
        scene_id=chunk.scene_id,
        language=chunk.language,
        visibility=chunk.visibility,
        ord=chunk.ord,
        text=chunk.text,
        embedding=chunk.embedding,
        embedding_model=chunk.embedding_model,
    )


def _new_chunk(
    entity: ScenarioEntityRow,
    chunk: Chunk,
    vector: list[float],
    model_name: str,
    version_id: uuid.UUID,
) -> ScenarioChunkRow:
    return ScenarioChunkRow(
        version_id=version_id,
        entity_id=entity.id,
        type=chunk.type,
        key=chunk.key,
        scene_id=chunk.scene_id,
        language=chunk.language,
        visibility=chunk.visibility,
        ord=chunk.ord,
        text=chunk.text,
        embedding=vector,
        embedding_model=model_name,
    )
