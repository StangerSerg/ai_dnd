"""Настройки identity: секрет сессий, TTL, SMTP и OAuth-ключи (env AI_DND_, .env)."""

from functools import lru_cache

import structlog
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_dnd.config import Environment

logger = structlog.get_logger(__name__)

DEV_SECRET_KEY = "dev-insecure-identity-secret-change-me"


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_DND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    # Демо-заглушка (#32): регистрация сразу подтверждает email и пускает без
    # письма-подтверждения. Только для демо/dev; в проде флаг выключен.
    demo_auth: bool = False
    session_ttl_days: int = 30
    email_token_ttl_hours: int = 48
    invite_token_ttl_days: int = 7
    base_url: str = "http://localhost:8000"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None


@lru_cache
def get_identity_settings() -> IdentitySettings:
    """Настройки identity, прочитанные один раз за время жизни процесса."""
    settings = IdentitySettings()
    if settings.secret_key.get_secret_value() == DEV_SECRET_KEY:
        logger.warning("identity_dev_secret_key", hint="задайте AI_DND_SECRET_KEY в .env")
    return settings
