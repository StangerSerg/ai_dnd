"""Локальные эмбеддинги через sentence-transformers (архитектура 8.4, 10).

Модель многоязычная, считается на CPU в воркере, бесплатно и без сети при индексации.
Модель загружается лениво и один раз на процесс. Имя модели пишется в метаданные
индекса: сменили модель — переиндексировали.
"""

from collections.abc import Sequence
from typing import Any


class Embedder:
    def __init__(self, model_name: str, dim: int) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Нормализованные векторы для косинусной близости в pgvector."""
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return [[float(x) for x in vector] for vector in vectors]
