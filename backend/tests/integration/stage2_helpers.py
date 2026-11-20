"""Общие помощники интеграционных тестов этапа 2 (pytest не собирает этот модуль).

Тесты работают ТОЛЬКО с базой ai_dnd_test: боевая база ai_dnd не затрагивается.
"""

from __future__ import annotations

import math
import os
import random
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import PostgresDsn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.game.content import GameContent
from ai_dnd.rag.chunking import chunk_entity
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.scenarios.models import ScenarioEntityRow
from ai_dnd.scenarios.schemas import Scenario

TEST_DATABASE_URL = os.environ.get(
    "AI_DND_TEST_DATABASE_URL",
    "postgresql+asyncpg://ai_dnd:ai_dnd@localhost:5432/ai_dnd_test",
)
# Страховка от случайного запуска против боевой базы.
if not (PostgresDsn(TEST_DATABASE_URL).path or "").lstrip("/").endswith("_test"):
    raise RuntimeError(f"Интеграционные тесты допускают только *_test базу: {TEST_DATABASE_URL}")


class StubEmbedder(Embedder):
    """Детерминированные векторы без загрузки модели.

    spread=False: все тексты получают одинаковый вектор (косинусная близость 1) —
    все фрагменты «релевантны», удобно проверять фильтр видимости в SQL.
    spread=True: вектор зависит от текста, близость разных текстов ~0 — порог
    релевантности проходят только полнотекстовые совпадения.
    """

    def __init__(self, model_name: str, dim: int = 384, *, spread: bool = False) -> None:
        super().__init__(model_name, dim)
        self._spread = spread

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._spread:
            return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        rng = random.Random(text)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]


def canon_scenario(scenario: Scenario) -> dict[str, Any]:
    """Сценарий в каноническом виде: порядок сущностей не должен влиять на сравнение."""
    data = scenario.model_dump(mode="json")
    for key in ("scenes", "npcs", "items", "clues", "handouts", "encounters", "lore"):
        data[key] = sorted(data[key], key=lambda entity: entity["id"])
    return data


def canon_content(content: GameContent) -> dict[str, Any]:
    return {
        "ruleset": content.ruleset.model_dump(mode="json"),
        "template": content.template.template.model_dump(mode="json"),
        "bestiaries": [b.model_dump(mode="json") for b in content.bestiaries],
        "scenario": canon_scenario(content.scenario),
        "glossary": content.glossary.model_dump(mode="json"),
    }


async def expected_chunks(
    sessions: async_sessionmaker[AsyncSession], version_id: uuid.UUID, language: str
) -> int:
    """Сколько фрагментов должна дать нарезка всех сущностей версии."""
    async with sessions() as db:
        rows = list(
            await db.scalars(
                select(ScenarioEntityRow).where(ScenarioEntityRow.version_id == version_id)
            )
        )
    return sum(len(chunk_entity(row.type, row.key, row.data, language)) for row in rows)
