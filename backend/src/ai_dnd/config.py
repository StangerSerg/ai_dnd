"""Настройки приложения: переменные окружения с префиксом AI_DND_ и файл .env."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["console", "json"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_DND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    log_level: LogLevel = "INFO"
    # console — цветной вывод для разработки, json — для сервера и сбора логов.
    log_format: LogFormat = "console"

    # Значения по умолчанию совпадают с infra/docker-compose.yml.
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://ai_dnd:ai_dnd@localhost:5432/ai_dnd"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Встроенный контент: шаблоны карточек, готовые персонажи, наборы правил, сценарии.
    # По умолчанию — папка content/ в корне репозитория.
    content_dir: Path = Path(__file__).resolve().parents[3] / "content"


@lru_cache
def get_settings() -> Settings:
    """Настройки, прочитанные один раз за время жизни процесса."""
    return Settings()
