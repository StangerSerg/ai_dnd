import pytest
from pydantic import ValidationError

from ai_dnd.config import Environment, Settings


def make_settings() -> Settings:
    # Без файла .env, чтобы локальные настройки разработчика не влияли на тесты.
    return Settings(_env_file=None)


def test_defaults_point_to_local_services() -> None:
    settings = make_settings()

    assert settings.environment is Environment.DEV
    assert settings.log_level == "INFO"
    assert settings.database_url.scheme == "postgresql+asyncpg"
    assert settings.redis_url.host == "localhost"


def test_values_come_from_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DND_ENVIRONMENT", "prod")
    monkeypatch.setenv("AI_DND_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AI_DND_DATABASE_URL", "postgresql+asyncpg://user:secret@db:5432/game")

    settings = make_settings()

    assert settings.environment is Environment.PROD
    assert settings.log_level == "WARNING"
    assert settings.database_url.hosts()[0]["host"] == "db"


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DND_ENVIRONMENT", "staging")

    with pytest.raises(ValidationError):
        make_settings()
