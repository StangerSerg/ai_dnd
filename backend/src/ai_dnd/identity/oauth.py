"""OAuth через Authlib (Discord, Google). Без ключей провайдер честно не настроен."""

from typing import Any, cast

from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]
from fastapi import Request

from ai_dnd.identity.settings import IdentitySettings

__all__ = ["PROVIDERS", "OAuth", "build_oauth", "fetch_userinfo", "get_client"]

PROVIDERS = ("discord", "google")


def build_oauth(settings: IdentitySettings) -> OAuth:
    """Регистрирует только провайдеров с ключами; сетевых вызовов здесь нет (metadata lazy)."""
    oauth = OAuth()
    if settings.discord_client_id and settings.discord_client_secret:
        oauth.register(
            name="discord",
            client_id=settings.discord_client_id,
            client_secret=settings.discord_client_secret.get_secret_value(),
            server_metadata_url="https://discord.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid identify email"},
        )
    if settings.google_client_id and settings.google_client_secret:
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return oauth


def get_client(oauth: OAuth, provider: str) -> Any | None:
    if provider not in PROVIDERS:
        return None
    return oauth.create_client(provider)


async def fetch_userinfo(oauth: OAuth, provider: str, request: Request) -> dict[str, Any]:
    """Обменивает code на токен и возвращает userinfo провайдера (реальная сеть)."""
    client = oauth.create_client(provider)
    token = await client.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await client.userinfo(token=token)
    return cast(dict[str, Any], dict(userinfo))
