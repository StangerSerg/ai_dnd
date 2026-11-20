"""Контракт локальных эмбеддингов (подэтап 2.2, offline).

Реальная модель sentence-transformers не загружается: импорт подменяется стабом,
проверяются ленивая загрузка, нормализация и параметры encode.
"""

import math
import sys
import types
from collections.abc import Callable, Sequence

import pytest

from ai_dnd.rag.embeddings import Embedder


class _StubModel:
    """Записывает вызовы encode и отдаёт детерминированные единичные векторы."""

    def __init__(self, model_name: str, device: str) -> None:
        self.model_name = model_name
        self.device = device
        self.calls: list[dict[str, object]] = []

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append({"texts": texts, **kwargs})
        # Единичные векторы: нормализация уже выполнена «моделью».
        return [[1, 0, 0] for _ in texts]


class _StubModule(types.ModuleType):
    """Типизированная замена модуля sentence_transformers."""

    SentenceTransformer: Callable[..., _StubModel]


@pytest.fixture
def stub_models(monkeypatch: pytest.MonkeyPatch) -> list[_StubModel]:
    """Подменяет модуль sentence_transformers factory-функцией со стаб-моделью."""
    created: list[_StubModel] = []

    def factory(model_name: str, *, device: str = "cpu") -> _StubModel:
        model = _StubModel(model_name, device)
        created.append(model)
        return model

    module = _StubModule("sentence_transformers")
    module.SentenceTransformer = factory
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return created


def test_model_loaded_lazily_and_once(stub_models: list[_StubModel]) -> None:
    embedder = Embedder("stub-model", 3)
    assert stub_models == []

    embedder.embed(["первый"])
    assert len(stub_models) == 1
    assert stub_models[0].model_name == "stub-model"
    assert stub_models[0].device == "cpu"

    embedder.embed(["второй"])
    assert len(stub_models) == 1


def test_encode_contract(stub_models: list[_StubModel]) -> None:
    embedder = Embedder("stub-model", 3)
    texts: Sequence[str] = ("культ змея", "старая мельница")
    embedder.embed(texts)

    assert len(stub_models[0].calls) == 1
    call = stub_models[0].calls[0]
    assert call["texts"] == ["культ змея", "старая мельница"]
    assert call["normalize_embeddings"] is True
    assert call["show_progress_bar"] is False


def test_empty_texts_skip_model(stub_models: list[_StubModel]) -> None:
    embedder = Embedder("stub-model", 3)

    assert embedder.embed([]) == []
    assert stub_models == []


def test_vectors_are_plain_floats(stub_models: list[_StubModel]) -> None:
    embedder = Embedder("stub-model", 3)
    vectors = embedder.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(isinstance(x, float) for vector in vectors for x in vector)


def test_normalized_vectors_passed_through(stub_models: list[_StubModel]) -> None:
    embedder = Embedder("stub-model", 3)
    vectors = embedder.embed(["a", "b"])

    # encode вызывается с normalize_embeddings=True, векторы идут в pgvector как есть.
    for vector in vectors:
        assert math.sqrt(sum(x * x for x in vector)) == pytest.approx(1.0)


def test_attributes_kept_for_index_metadata() -> None:
    embedder = Embedder("stub-model", 384)

    assert embedder.model_name == "stub-model"
    assert embedder.dim == 384
