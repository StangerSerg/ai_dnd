"""Подэтап 2.3 (SQL-часть): гибридный поиск и фильтр видимости в SQL, lookup_lore.

Чистые функции RRF/бюджета здесь не тестируются. Стаб-энкодеры изолируют SQL:
постоянный вектор делает все фрагменты «релевантными», разбросанные векторы —
оставляют только полнотекстовые совпадения. Один тест — с реальной моделью.

Запуск: pytest tests/integration -m integration (нужны PostgreSQL+pgvector из infra/).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.rag.embeddings import Embedder
from ai_dnd.rag.index import index_version
from ai_dnd.rag.search import LoreSearcher, hybrid_search
from ai_dnd.scenarios.store import publish_scenario

from .stage2_helpers import StubEmbedder

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]

DM_VIS = ["public", "dm"]
PLAYER_VIS = ["public"]


async def _indexed(
    sessions: Sessions,
    scenario_dir: tuple[Path, str],
    embedder: Embedder,
    language: str,
) -> uuid.UUID:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, language)
    await index_version(sessions, version, embedder=embedder)
    return version


async def test_visibility_filter_in_sql(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, stub_embedder, "ru")
    query = "культ змея и старая мельница"

    # limit/candidates больше числа фрагментов (~32): при постоянном векторе все
    # расстояния равны, и усечение по недетерминированному порядку БД делало тест флейком.
    dm_hits = await hybrid_search(
        sessions,
        version,
        query,
        embedder=stub_embedder,
        allowed_visibility=DM_VIS,
        language="ru",
        limit=100,
        candidates=200,
    )
    player_hits = await hybrid_search(
        sessions,
        version,
        query,
        embedder=stub_embedder,
        allowed_visibility=PLAYER_VIS,
        language="ru",
        limit=100,
        candidates=200,
    )
    assert dm_hits
    assert {hit.visibility for hit in dm_hits} == {"public", "dm"}
    assert player_hits
    assert all(hit.visibility == "public" for hit in player_hits)

    # LoreSearcher: ведущий видит dm, игрок — только public.
    searcher = LoreSearcher(sessions, version, stub_embedder, "ru")
    assert any(hit.visibility == "dm" for hit in await searcher.search(query, limit=100))
    player_lore = await searcher.search(query, player=True, limit=100)
    assert player_lore
    assert all(hit.visibility == "public" for hit in player_lore)


async def test_fts_dm_term_hidden_from_player(
    sessions: Sessions, scenario_dir: tuple[Path, str], fts_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, fts_embedder, "ru")
    # «Воронья башня» встречается только в dm-текстах (заметки сцены, секреты NPC).
    query = "Воронья башня"

    dm_hits = await hybrid_search(
        sessions, version, query, embedder=fts_embedder, allowed_visibility=DM_VIS, language="ru"
    )
    assert dm_hits
    assert all(hit.visibility == "dm" for hit in dm_hits)
    player_hits = await hybrid_search(
        sessions,
        version,
        query,
        embedder=fts_embedder,
        allowed_visibility=PLAYER_VIS,
        language="ru",
    )
    assert player_hits == []


async def test_lookup_lore_finds_public_lore(
    sessions: Sessions, scenario_dir: tuple[Path, str], fts_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, fts_embedder, "ru")
    searcher = LoreSearcher(sessions, version, fts_embedder, "ru")
    # «река Сонная» — только в публичном lore о деревне.
    hits = await searcher.search("река Сонная", player=True)
    assert any(hit.type == "lore" and hit.key == "village" for hit in hits)
    assert all(hit.visibility == "public" for hit in hits)


async def test_lookup_lore_dm_lore_hidden_from_player(
    sessions: Sessions, scenario_dir: tuple[Path, str], fts_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, fts_embedder, "ru")
    searcher = LoreSearcher(sessions, version, fts_embedder, "ru")
    # «новолуние» есть только в dm-материалах, включая dm-lore о культе.
    dm_hits = await searcher.search("новолуние")
    assert any(hit.type == "lore" and hit.key == "serpent_cult" for hit in dm_hits)
    assert all(hit.visibility == "dm" for hit in dm_hits)
    assert await searcher.search("новолуние", player=True) == []


async def test_en_fts_and_visibility(
    sessions: Sessions, scenario_dir: tuple[Path, str], fts_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, fts_embedder, "en")

    dm_hits = await hybrid_search(
        sessions,
        version,
        "Raven Tower",
        embedder=fts_embedder,
        allowed_visibility=DM_VIS,
        language="en",
    )
    assert dm_hits
    assert all(hit.visibility == "dm" for hit in dm_hits)
    assert (
        await hybrid_search(
            sessions,
            version,
            "Raven Tower",
            embedder=fts_embedder,
            allowed_visibility=PLAYER_VIS,
            language="en",
        )
        == []
    )

    player_hits = await hybrid_search(
        sessions,
        version,
        "Sleepy River",
        embedder=fts_embedder,
        allowed_visibility=PLAYER_VIS,
        language="en",
    )
    assert any(hit.type == "lore" and hit.key == "village" for hit in player_hits)
    assert all(hit.visibility == "public" for hit in player_hits)


async def test_real_model_relevance_and_threshold(
    sessions: Sessions, scenario_dir: tuple[Path, str], real_embedder: Embedder
) -> None:
    version = await _indexed(sessions, scenario_dir, real_embedder, "ru")

    hits = await hybrid_search(
        sessions,
        version,
        "культ змея и старая мельница",
        embedder=real_embedder,
        allowed_visibility=DM_VIS,
        language="ru",
    )
    assert hits
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))

    player_hits = await hybrid_search(
        sessions,
        version,
        "культ змея и старая мельница",
        embedder=real_embedder,
        allowed_visibility=PLAYER_VIS,
        language="ru",
    )
    assert all(hit.visibility == "public" for hit in player_hits)

    irrelevant = await hybrid_search(
        sessions,
        version,
        "аэропорт терминал регистрация рейс космодром",
        embedder=real_embedder,
        allowed_visibility=DM_VIS,
        language="ru",
    )
    assert irrelevant == []


async def test_empty_query_and_visibility(
    sessions: Sessions, scenario_dir: tuple[Path, str], stub_embedder: StubEmbedder
) -> None:
    version = await _indexed(sessions, scenario_dir, stub_embedder, "ru")
    assert (
        await hybrid_search(
            sessions,
            version,
            "   ",
            embedder=stub_embedder,
            allowed_visibility=DM_VIS,
            language="ru",
        )
        == []
    )
    assert (
        await hybrid_search(
            sessions, version, "культ", embedder=stub_embedder, allowed_visibility=[], language="ru"
        )
        == []
    )
