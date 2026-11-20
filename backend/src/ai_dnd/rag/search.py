"""Гибридный поиск по фрагментам сценария (архитектура 9.2).

Векторный поиск pgvector (смысловая близость) плюс полнотекстовый PostgreSQL с морфологией
языка (имена, точные термины). Списки объединяются Reciprocal Rank Fusion, фильтр
видимости стоит в SQL: для игрока фрагменты ``dm`` даже не выбираются из базы. Порог
релевантности отсекает пустой слой: если ничего близкого нет, поиск возвращает [].
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Float, desc, literal, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from ai_dnd.rag.embeddings import Embedder
from ai_dnd.scenarios.models import ScenarioChunkRow

Sessions = async_sessionmaker[AsyncSession]

# Константа сглаживания RRF: стандартное значение 60.
RRF_K = 60
_LANG_CONFIG = {"ru": "russian", "en": "english"}


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    type: str
    key: str
    scene_id: str | None
    visibility: str
    text: str
    score: float
    similarity: float


async def hybrid_search(
    sessions: Sessions,
    version_id: uuid.UUID,
    query: str,
    *,
    embedder: Embedder,
    allowed_visibility: Sequence[str],
    language: str,
    limit: int = 8,
    candidates: int = 30,
    min_similarity: float = 0.35,
) -> list[SearchHit]:
    """Ранжированные фрагменты, доступные по видимости. Пусто, если нет релевантных."""
    query = query.strip()
    if not query or not allowed_visibility:
        return []
    allowed = tuple(allowed_visibility)
    vector = (await asyncio.to_thread(embedder.embed, [query]))[0]

    async with sessions() as db:
        vector_rows = await _vector_candidates(db, version_id, vector, allowed, candidates)
        fts_rows = await _fts_candidates(db, version_id, query, language, allowed, candidates)

    scores: dict[uuid.UUID, float] = {}
    similarity: dict[uuid.UUID, float] = {}
    chunks: dict[uuid.UUID, ScenarioChunkRow] = {}
    fts_ids: set[uuid.UUID] = set()
    for rank, (chunk, dist) in enumerate(vector_rows, start=1):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
        similarity[chunk.id] = 1.0 - dist
        chunks[chunk.id] = chunk
    for rank, chunk in enumerate(fts_rows, start=1):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
        chunks.setdefault(chunk.id, chunk)
        fts_ids.add(chunk.id)

    ranked = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    hits: list[SearchHit] = []
    for cid in ranked:
        # Релевантен, если близок по смыслу или найден полнотекстово (имя/термин).
        if similarity.get(cid, 0.0) < min_similarity and cid not in fts_ids:
            continue
        chunk = chunks[cid]
        hits.append(
            SearchHit(
                chunk_id=cid,
                type=chunk.type,
                key=chunk.key,
                scene_id=chunk.scene_id,
                visibility=chunk.visibility,
                text=chunk.text,
                score=scores[cid],
                similarity=similarity.get(cid, 0.0),
            )
        )
        if len(hits) >= limit:
            break
    return hits


async def _vector_candidates(
    db: AsyncSession,
    version_id: uuid.UUID,
    vector: list[float],
    allowed: tuple[str, ...],
    candidates: int,
) -> list[tuple[ScenarioChunkRow, float]]:
    distance = ScenarioChunkRow.embedding.cosine_distance(vector)
    stmt = (
        select(ScenarioChunkRow, distance.label("dist"))
        .where(
            ScenarioChunkRow.version_id == version_id,
            ScenarioChunkRow.visibility.in_(allowed),
        )
        .order_by(distance)
        .limit(candidates)
    )
    rows = await db.execute(stmt)
    return [(cast(ScenarioChunkRow, row[0]), float(row[1])) for row in rows.all()]


async def _fts_candidates(
    db: AsyncSession,
    version_id: uuid.UUID,
    query: str,
    language: str,
    allowed: tuple[str, ...],
    candidates: int,
) -> list[ScenarioChunkRow]:
    config = sql_cast(literal(_LANG_CONFIG.get(language, "english")), REGCONFIG)
    tsquery = func.plainto_tsquery(config, query)
    rank = func.ts_rank(ScenarioChunkRow.tsv, tsquery).cast(Float)
    stmt = (
        select(ScenarioChunkRow)
        .where(
            ScenarioChunkRow.version_id == version_id,
            ScenarioChunkRow.visibility.in_(allowed),
            ScenarioChunkRow.tsv.op("@@")(tsquery),
        )
        .order_by(desc(rank))
        .limit(candidates)
    )
    return list(await db.scalars(stmt))


# Бюджет слоя найденных фрагментов (архитектура 9.1: слой 7 ≈ 0–1,5 тыс. токенов).
# Грубая оценка: ~3 символа на токен для смеси русского и английского.
FRAGMENT_BUDGET_TOKENS = 1500
_CHARS_PER_TOKEN = 3


class LoreSearcher:
    """Поиск по опубликованной версии сценария для ведущего или игрока."""

    def __init__(
        self,
        sessions: Sessions,
        version_id: uuid.UUID,
        embedder: Embedder,
        language: str,
    ) -> None:
        self._sessions = sessions
        self._version_id = version_id
        self._embedder = embedder
        self._language = language

    async def search(self, query: str, *, player: bool = False, limit: int = 6) -> list[SearchHit]:
        allowed = ["public"] if player else ["public", "dm"]
        return await hybrid_search(
            self._sessions,
            self._version_id,
            query,
            embedder=self._embedder,
            allowed_visibility=allowed,
            language=self._language,
            limit=limit,
        )


def format_fragments(
    hits: Sequence[SearchHit], budget_tokens: int = FRAGMENT_BUDGET_TOKENS
) -> list[str]:
    """Строки фрагментов для промпта в пределах бюджета токенов, без дублей по тексту."""
    budget_chars = budget_tokens * _CHARS_PER_TOKEN
    lines: list[str] = []
    seen: set[str] = set()
    used = 0
    for hit in hits:
        text = hit.text.strip()
        if not text or text in seen:
            continue
        if used + len(text) > budget_chars:
            break
        seen.add(text)
        used += len(text)
        label = f"{hit.type}:{hit.key}" + (f" [{hit.visibility}]" if hit.visibility == "dm" else "")
        lines.append(f"- ({label}) {text}")
    return lines
