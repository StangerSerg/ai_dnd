"""Фикстуры интеграционных тестов этапа 2: только база ai_dnd_test.

Каждый тест публикует сценарий под уникальным slug во временной копии content/
и удаляет свои строки после себя. Строки rulesets/sheet_templates переиспользуются
upsert'ом по (name, version) — это штатное поведение, мусор не накапливается.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import PostgresDsn
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_dnd.config import Settings
from ai_dnd.db.engine import create_engine, create_session_factory
from ai_dnd.game.models import GameSessionRow
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.scenarios.models import ScenarioRow

from .stage2_helpers import TEST_DATABASE_URL, StubEmbedder

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def db_settings() -> Settings:
    return Settings(_env_file=None, database_url=PostgresDsn(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def migrated_db(db_settings: Settings) -> None:
    """Схема ai_dnd_test на head (миграции идемпотентны)."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(db_settings.database_url))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture
async def db_engine(db_settings: Settings, migrated_db: None) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
async def scenario_dir(
    tmp_path: Path,
    content_dir: Path,
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[Path, str]]:
    """Временная копия content/ со сценарием под уникальным slug + очистка строк теста."""
    slug = f"it2-{uuid.uuid4().hex[:12]}"
    target = tmp_path / "content"
    for sub in ("rulesets", "sheets", "bestiary"):
        shutil.copytree(content_dir / sub, target / sub)
    shutil.copy2(content_dir / "glossary.yaml", target / "glossary.yaml")
    scenarios = target / "scenarios"
    scenarios.mkdir()
    for lang in ("ru", "en"):
        shutil.copy2(
            content_dir / "scenarios" / f"shadow-over-millford.{lang}.yaml",
            scenarios / f"{slug}.{lang}.yaml",
        )
    try:
        yield target, slug
    finally:
        async with sessions() as db:
            # Сессии ссылаются на версии без CASCADE — удаляем первыми.
            await db.execute(delete(GameSessionRow).where(GameSessionRow.scenario == slug))
            # versions/entities/chunks удаляются каскадом от scenarios.
            await db.execute(delete(ScenarioRow).where(ScenarioRow.slug == slug))
            await db.commit()


@pytest.fixture
def stub_embedder() -> StubEmbedder:
    """Постоянный вектор: все фрагменты релевантны (близость 1)."""
    return StubEmbedder("stub-constant")


@pytest.fixture
def fts_embedder() -> StubEmbedder:
    """Разные векторы: порог релевантности проходят только FTS-совпадения."""
    return StubEmbedder("stub-spread", spread=True)


@pytest.fixture(scope="session")
def real_embedder(db_settings: Settings) -> Embedder:
    """Реальная закэшированная модель; грузится лениво один раз на процесс."""
    return Embedder(db_settings.embedding_model, db_settings.embedding_dim)
