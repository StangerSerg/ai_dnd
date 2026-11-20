"""Гибридный поиск: чистая логика поверх стабов (подэтап 2.3, offline).

SQL-кандидаты (_vector_candidates/_fts_candidates) подменяются детерминированными
списками: проверяются RRF-слияние, порог релевантности, лимит, выбор видимости
в LoreSearcher и бюджет format_fragments. Сам PostgreSQL/pgvector — зона integration.
"""

import uuid
from collections.abc import Sequence
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_dnd.rag import search
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.rag.search import (
    FRAGMENT_BUDGET_TOKENS,
    RRF_K,
    LoreSearcher,
    SearchHit,
    format_fragments,
    hybrid_search,
)
from ai_dnd.scenarios.models import EMBEDDING_DIM, ScenarioChunkRow

VERSION_ID = uuid.UUID(int=1000)


class _FakeSession:
    """Пустая async-сессия: SQL-кандидаты застаблены, обращения к базе нет."""

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _fake_sessions() -> _FakeSession:
    return _FakeSession()


def _sessions() -> search.Sessions:
    return cast(search.Sessions, _fake_sessions)


def _row(n: int, *, visibility: str = "public", text: str | None = None) -> ScenarioChunkRow:
    return ScenarioChunkRow(
        id=uuid.UUID(int=n),
        version_id=VERSION_ID,
        entity_id=uuid.UUID(int=2000 + n),
        type="lore",
        key=f"key{n}",
        scene_id=None,
        language="ru",
        visibility=visibility,
        ord=0,
        text=text if text is not None else f"Фрагмент {n} про культ змея.",
        embedding=[0.0] * EMBEDDING_DIM,
        embedding_model="stub-model",
    )


def _stub_embed(texts: Sequence[str]) -> list[list[float]]:
    return [[0.0] * EMBEDDING_DIM for _ in texts]


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> Embedder:
    instance = Embedder("stub-model", EMBEDDING_DIM)
    monkeypatch.setattr(instance, "embed", _stub_embed)
    return instance


def _patch_candidates(
    monkeypatch: pytest.MonkeyPatch,
    vector_rows: list[tuple[ScenarioChunkRow, float]],
    fts_rows: list[ScenarioChunkRow],
) -> None:
    async def fake_vector(
        db: AsyncSession,
        version_id: uuid.UUID,
        vector: list[float],
        allowed: tuple[str, ...],
        candidates: int,
    ) -> list[tuple[ScenarioChunkRow, float]]:
        return vector_rows

    async def fake_fts(
        db: AsyncSession,
        version_id: uuid.UUID,
        query: str,
        language: str,
        allowed: tuple[str, ...],
        candidates: int,
    ) -> list[ScenarioChunkRow]:
        return fts_rows

    monkeypatch.setattr(search, "_vector_candidates", fake_vector)
    monkeypatch.setattr(search, "_fts_candidates", fake_fts)


# --- RRF-слияние и порог релевантности ---


async def test_rrf_order_and_scores(monkeypatch: pytest.MonkeyPatch, embedder: Embedder) -> None:
    a, b, c = _row(1), _row(2), _row(3)
    _patch_candidates(monkeypatch, [(a, 0.1), (b, 0.2), (c, 0.3)], [a])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public", "dm"],
        language="ru",
    )

    assert [h.key for h in hits] == ["key1", "key2", "key3"]
    # A найден обоими списками (ранг 1 в каждом), B и C — только вектором.
    assert hits[0].score == pytest.approx(2.0 / (RRF_K + 1))
    assert hits[1].score == pytest.approx(1.0 / (RRF_K + 2))
    assert hits[2].score == pytest.approx(1.0 / (RRF_K + 3))
    assert hits[0].score > hits[1].score > hits[2].score


async def test_duplicate_chunk_merged_once(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    a = _row(1)
    _patch_candidates(monkeypatch, [(a, 0.1)], [a])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert len(hits) == 1
    assert hits[0].chunk_id == a.id
    assert hits[0].score == pytest.approx(2.0 / (RRF_K + 1))
    assert hits[0].similarity == pytest.approx(0.9)


async def test_similarity_is_one_minus_distance(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    _patch_candidates(monkeypatch, [(_row(1), 0.25)], [])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert hits[0].similarity == pytest.approx(0.75)


async def test_fts_only_hit_kept_with_zero_similarity(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    # Точный термин найден полнотекстово, векторной близости нет — всё равно релевантен.
    _patch_candidates(monkeypatch, [], [_row(7)])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "мельница",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert len(hits) == 1
    assert hits[0].key == "key7"
    assert hits[0].similarity == 0.0
    assert hits[0].score == pytest.approx(1.0 / (RRF_K + 1))


async def test_low_similarity_vector_only_filtered(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    _patch_candidates(monkeypatch, [(_row(1), 0.9)], [])  # similarity 0.1 < 0.35

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "аэропорт",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert hits == []


async def test_similarity_threshold_inclusive(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    _patch_candidates(monkeypatch, [(_row(1), 0.65)], [])  # similarity ровно 0.35

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert len(hits) == 1


async def test_fts_rescues_low_similarity_chunk(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    a = _row(1)
    _patch_candidates(monkeypatch, [(a, 0.9)], [a])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
    )

    assert len(hits) == 1
    assert hits[0].similarity == pytest.approx(0.1)


async def test_custom_min_similarity(monkeypatch: pytest.MonkeyPatch, embedder: Embedder) -> None:
    _patch_candidates(monkeypatch, [(_row(1), 0.2)], [])  # similarity 0.8

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
        min_similarity=0.9,
    )

    assert hits == []


async def test_limit_truncates_ranked_list(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    rows = [(_row(n), 0.01 * n) for n in range(1, 6)]
    _patch_candidates(monkeypatch, rows, [])

    hits = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
        limit=2,
    )

    assert [h.key for h in hits] == ["key1", "key2"]


async def test_empty_inputs_skip_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_embed(texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("Эмбеддинг не должен считаться для пустого входа")

    instance = Embedder("stub-model", EMBEDDING_DIM)
    monkeypatch.setattr(instance, "embed", forbidden_embed)
    _patch_candidates(monkeypatch, [], [])

    empty_query = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "   ",
        embedder=instance,
        allowed_visibility=["public"],
        language="ru",
    )
    no_visibility = await hybrid_search(
        _sessions(),
        VERSION_ID,
        "культ",
        embedder=instance,
        allowed_visibility=[],
        language="ru",
    )

    assert empty_query == []
    assert no_visibility == []


async def test_filters_forwarded_to_sql(
    monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    seen: dict[str, object] = {}

    async def fake_vector(
        db: AsyncSession,
        version_id: uuid.UUID,
        vector: list[float],
        allowed: tuple[str, ...],
        candidates: int,
    ) -> list[tuple[ScenarioChunkRow, float]]:
        seen["vector"] = (version_id, allowed, candidates)
        return []

    async def fake_fts(
        db: AsyncSession,
        version_id: uuid.UUID,
        query: str,
        language: str,
        allowed: tuple[str, ...],
        candidates: int,
    ) -> list[ScenarioChunkRow]:
        seen["fts"] = (version_id, query, language, allowed, candidates)
        return []

    monkeypatch.setattr(search, "_vector_candidates", fake_vector)
    monkeypatch.setattr(search, "_fts_candidates", fake_fts)

    await hybrid_search(
        _sessions(),
        VERSION_ID,
        " культ змея ",
        embedder=embedder,
        allowed_visibility=["public"],
        language="ru",
        candidates=17,
    )

    assert seen["vector"] == (VERSION_ID, ("public",), 17)
    assert seen["fts"] == (VERSION_ID, "культ змея", "ru", ("public",), 17)


# --- LoreSearcher: выбор видимости (чистая логика) ---


def _hit(
    text: str, *, visibility: str = "public", key: str = "k", type_: str = "lore"
) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.UUID(int=1),
        type=type_,
        key=key,
        scene_id=None,
        visibility=visibility,
        text=text,
        score=1.0,
        similarity=1.0,
    )


@pytest.mark.parametrize(
    ("player", "expected"),
    [(True, ["public"]), (False, ["public", "dm"])],
)
async def test_lore_searcher_visibility_selection(
    monkeypatch: pytest.MonkeyPatch, player: bool, expected: list[str]
) -> None:
    seen: dict[str, object] = {}
    expected_hits = [_hit("Культ Змея.")]

    async def fake_hybrid(
        sessions: search.Sessions,
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
        seen.update(
            version_id=version_id,
            query=query,
            allowed=list(allowed_visibility),
            language=language,
            limit=limit,
        )
        return expected_hits

    monkeypatch.setattr(search, "hybrid_search", fake_hybrid)
    searcher = LoreSearcher(_sessions(), VERSION_ID, Embedder("stub-model", EMBEDDING_DIM), "ru")

    hits = await searcher.search("культ змея", player=player, limit=4)

    assert hits == expected_hits
    assert seen["allowed"] == expected
    assert seen["version_id"] == VERSION_ID
    assert seen["query"] == "культ змея"
    assert seen["language"] == "ru"
    assert seen["limit"] == 4


async def test_lore_searcher_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_hybrid(
        sessions: search.Sessions,
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
        seen["limit"] = limit
        return []

    monkeypatch.setattr(search, "hybrid_search", fake_hybrid)
    searcher = LoreSearcher(_sessions(), VERSION_ID, Embedder("stub-model", EMBEDDING_DIM), "en")

    await searcher.search("serpent cult")

    assert seen["limit"] == 6


# --- format_fragments: бюджет, дедупликация, формат ---


def test_format_labels_public_and_dm() -> None:
    hits = [
        _hit("Деревня у брода.", key="village"),
        _hit("Ключ на поясе стража.", visibility="dm", key="cellar_key", type_="item"),
    ]

    lines = format_fragments(hits)

    assert lines == [
        "- (lore:village) Деревня у брода.",
        "- (item:cellar_key [dm]) Ключ на поясе стража.",
    ]


def test_format_deduplicates_by_text() -> None:
    hits = [_hit("Один и тот же текст.", key="a"), _hit("Один и тот же текст.", key="b")]

    lines = format_fragments(hits)

    assert lines == ["- (lore:a) Один и тот же текст."]


def test_format_skips_blank_text_and_strips() -> None:
    hits = [_hit("   "), _hit(""), _hit("  Текст с пробелами.  ")]

    lines = format_fragments(hits)

    assert lines == ["- (lore:k) Текст с пробелами."]


def test_format_budget_stops_at_first_overflow() -> None:
    # Бюджет 10 токенов = 30 символов: первый фрагмент (20) влезает, второй (15) — нет.
    hits = [_hit("x" * 20, key="a"), _hit("y" * 15, key="b"), _hit("z" * 5, key="c")]

    lines = format_fragments(hits, budget_tokens=10)

    # Обрезка — именно остановка: более короткий третий фрагмент уже не берётся.
    assert lines == [f"- (lore:a) {'x' * 20}"]


def test_format_budget_counts_text_only() -> None:
    # Метка строки не расходует бюджет: текст ровно в 30 символов проходит при 10 токенах.
    lines = format_fragments([_hit("x" * 30)], budget_tokens=10)

    assert lines == [f"- (lore:k) {'x' * 30}"]


def test_format_default_budget() -> None:
    limit_chars = FRAGMENT_BUDGET_TOKENS * 3
    fits = format_fragments([_hit("x" * limit_chars)])
    over = format_fragments([_hit("x" * (limit_chars + 1))])

    assert len(fits) == 1
    assert over == []


def test_format_empty_hits() -> None:
    assert format_fragments([]) == []
