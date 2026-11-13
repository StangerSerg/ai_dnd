"""Интеграционные тесты: нужны запущенные сервисы из infra/docker-compose.yml.

uv run pytest -m integration
"""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import PostgresDsn
from sqlalchemy import text

from ai_dnd.config import Settings
from ai_dnd.db.engine import create_engine
from ai_dnd.main import create_app

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get(
    "AI_DND_TEST_DATABASE_URL",
    "postgresql+asyncpg://ai_dnd:ai_dnd@localhost:5432/ai_dnd_test",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, database_url=PostgresDsn(TEST_DATABASE_URL))


async def installed_extensions(settings: Settings) -> set[str]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT extname FROM pg_extension"))
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


def test_migrations_enable_pgvector(settings: Settings) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(settings.database_url))
    config.attributes["configure_logger"] = False

    command.upgrade(config, "head")

    assert "vector" in asyncio.run(installed_extensions(settings))


def test_health_ok_with_real_services(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
