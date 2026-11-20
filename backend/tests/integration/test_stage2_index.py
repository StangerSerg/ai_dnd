"""Подэтап 2.2: индексация версии (scenario_chunks), reuse и смена модели эмбеддингов.

Воркер-задача index_scenario_version вызывается напрямую, без брокера и воркера.
Запуск: pytest tests/integration -m integration (нужны PostgreSQL+pgvector из infra/).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.config import get_settings
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.rag.index import index_version
from ai_dnd.scenarios.models import ScenarioChunkRow, ScenarioEntityRow
from ai_dnd.scenarios.store import publish_scenario
from ai_dnd.tasks import index_scenario_version

from .stage2_helpers import TEST_DATABASE_URL, StubEmbedder, expected_chunks

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]


async def test_index_creates_chunks_with_metadata(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, "ru")
    report = await index_version(sessions, version, embedder=stub_embedder)

    expected = await expected_chunks(sessions, version, "ru")
    assert report.entities > 0
    assert report.chunks == expected
    assert report.embedded == expected
    assert report.reused == 0

    async with sessions() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(ScenarioChunkRow)
            .where(ScenarioChunkRow.version_id == version)
        )
        dims = set(
            (
                await db.execute(
                    select(func.vector_dims(ScenarioChunkRow.embedding))
                    .where(ScenarioChunkRow.version_id == version)
                    .distinct()
                )
            ).scalars()
        )
        null_tsv = await db.scalar(
            select(func.count())
            .select_from(ScenarioChunkRow)
            .where(ScenarioChunkRow.version_id == version, ScenarioChunkRow.tsv.is_(None))
        )
        languages = set(
            (
                await db.execute(
                    select(ScenarioChunkRow.language)
                    .where(ScenarioChunkRow.version_id == version)
                    .distinct()
                )
            ).scalars()
        )
        models = set(
            (
                await db.execute(
                    select(ScenarioChunkRow.embedding_model)
                    .where(ScenarioChunkRow.version_id == version)
                    .distinct()
                )
            ).scalars()
        )
        scene_visibility = set(
            (
                await db.execute(
                    select(ScenarioChunkRow.visibility).where(
                        ScenarioChunkRow.version_id == version, ScenarioChunkRow.type == "scene"
                    )
                )
            ).scalars()
        )
        duplicates = (
            await db.execute(
                select(ScenarioChunkRow.entity_id, ScenarioChunkRow.ord)
                .where(ScenarioChunkRow.version_id == version)
                .group_by(ScenarioChunkRow.entity_id, ScenarioChunkRow.ord)
                .having(func.count() > 1)
            )
        ).all()

    assert total == expected
    assert dims == {384}
    assert null_tsv == 0
    assert languages == {"ru"}
    assert models == {"stub-constant"}
    # Видимость на уровне текста: у сцены public read_aloud и dm dm_notes.
    assert scene_visibility == {"public", "dm"}
    assert duplicates == []


async def test_reindex_same_version_replaces_chunks(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, "ru")
    first = await index_version(sessions, version, embedder=stub_embedder)
    second = await index_version(sessions, version, embedder=stub_embedder)

    assert second.chunks == first.chunks
    assert second.embedded == first.chunks
    assert second.reused == 0
    async with sessions() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(ScenarioChunkRow)
            .where(ScenarioChunkRow.version_id == version)
        )
    assert total == first.chunks


async def test_reuse_identical_previous_version(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    r1 = await index_version(sessions, v1, embedder=stub_embedder)
    v2 = await publish_scenario(sessions, content_path, slug, "ru")
    r2 = await index_version(sessions, v2, embedder=stub_embedder, previous_version_id=v1)

    assert r2.reused == r1.chunks
    assert r2.embedded == 0
    assert r2.chunks == r1.chunks
    async with sessions() as db:
        for version in (v1, v2):
            total = await db.scalar(
                select(func.count())
                .select_from(ScenarioChunkRow)
                .where(ScenarioChunkRow.version_id == version)
            )
            assert total == r1.chunks


async def test_changed_entity_reembedded_alone(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    r1 = await index_version(sessions, v1, embedder=stub_embedder)
    v2 = await publish_scenario(sessions, content_path, slug, "ru")

    async with sessions() as db:
        target = await db.scalar(
            select(ScenarioEntityRow)
            .where(ScenarioEntityRow.version_id == v2, ScenarioEntityRow.type == "lore")
            .limit(1)
        )
        assert target is not None
        new_data = dict(target.data)
        new_data["text"] = str(new_data.get("text", "")) + " Дополнительное сведение о культе."
        await db.execute(
            update(ScenarioEntityRow).where(ScenarioEntityRow.id == target.id).values(data=new_data)
        )
        await db.commit()
        changed_chunks = await db.scalar(
            select(func.count())
            .select_from(ScenarioChunkRow)
            .where(
                ScenarioChunkRow.version_id == v1,
                ScenarioChunkRow.type == "lore",
                ScenarioChunkRow.key == target.key,
            )
        )
    assert changed_chunks is not None
    assert changed_chunks > 0

    r2 = await index_version(sessions, v2, embedder=stub_embedder, previous_version_id=v1)
    assert r2.embedded == changed_chunks
    assert r2.reused == r1.chunks - changed_chunks
    assert r2.chunks == r1.chunks


async def test_previous_without_index_full_embed(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")  # без индексации
    v2 = await publish_scenario(sessions, content_path, slug, "ru")
    report = await index_version(sessions, v2, embedder=stub_embedder, previous_version_id=v1)

    expected = await expected_chunks(sessions, v2, "ru")
    assert report.chunks == expected
    assert report.embedded == expected
    assert report.reused == 0


async def test_model_change_forces_reembed(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    r1 = await index_version(sessions, v1, embedder=stub_embedder)
    v2 = await publish_scenario(sessions, content_path, slug, "ru")
    other_model = StubEmbedder("stub-other-model")
    r2 = await index_version(sessions, v2, embedder=other_model, previous_version_id=v1)

    assert r2.reused == 0
    assert r2.embedded == r1.chunks
    async with sessions() as db:
        models = set(
            (
                await db.execute(
                    select(ScenarioChunkRow.embedding_model).where(
                        ScenarioChunkRow.version_id == v2
                    )
                )
            ).scalars()
        )
    assert models == {"stub-other-model"}


async def test_dim_mismatch_rejected(sessions: Sessions, scenario_dir: tuple[Path, str]) -> None:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, "ru")
    with pytest.raises(ValueError, match="Размерность"):
        await index_version(sessions, version, embedder=StubEmbedder("bad-dim", dim=128))


async def test_unknown_version_raises(sessions: Sessions, stub_embedder: StubEmbedder) -> None:
    with pytest.raises(LookupError):
        await index_version(sessions, uuid.uuid4(), embedder=stub_embedder)


async def test_real_model_dim_and_normalization(
    sessions: Sessions, scenario_dir: tuple[Path, str], real_embedder: Embedder
) -> None:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, "ru")
    report = await index_version(sessions, version, embedder=real_embedder)
    assert report.chunks == await expected_chunks(sessions, version, "ru")

    async with sessions() as db:
        dims = set(
            (
                await db.execute(
                    select(func.vector_dims(ScenarioChunkRow.embedding))
                    .where(ScenarioChunkRow.version_id == version)
                    .distinct()
                )
            ).scalars()
        )
        models = set(
            (
                await db.execute(
                    select(ScenarioChunkRow.embedding_model).where(
                        ScenarioChunkRow.version_id == version
                    )
                )
            ).scalars()
        )
        vectors = list(
            await db.scalars(
                select(ScenarioChunkRow.embedding)
                .where(ScenarioChunkRow.version_id == version)
                .limit(5)
            )
        )
    assert dims == {384}
    assert models == {real_embedder.model_name}
    assert vectors
    for vector in vectors:
        norm = math.sqrt(sum(float(x) * float(x) for x in vector))
        assert norm == pytest.approx(1.0, abs=1e-4)


async def test_worker_task_direct_call(
    sessions: Sessions, scenario_dir: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задача вызывается напрямую; её настройки направлены в ai_dnd_test, энкодер — стаб."""
    content_path, slug = scenario_dir
    monkeypatch.setenv("AI_DND_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()

    def fake_embed(self: Embedder, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]

    monkeypatch.setattr(Embedder, "embed", fake_embed)
    try:
        v1 = await publish_scenario(sessions, content_path, slug, "ru")
        report = await index_scenario_version(str(v1))
        expected = await expected_chunks(sessions, v1, "ru")
        assert report["entities"] > 0
        assert report["chunks"] == expected
        assert report["reused"] == 0
        assert report["embedded"] == expected

        v2 = await publish_scenario(sessions, content_path, slug, "ru")
        reuse = await index_scenario_version(str(v2), str(v1))
        assert reuse["reused"] == expected
        assert reuse["embedded"] == 0

        async with sessions() as db:
            total = await db.scalar(
                select(func.count())
                .select_from(ScenarioChunkRow)
                .where(ScenarioChunkRow.version_id == v2)
            )
        assert total == expected
    finally:
        # Не оставляем тестовый URL в кэше настроек для остальных тестов.
        get_settings.cache_clear()
