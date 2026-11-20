"""Зависимости FastAPI: текущий пользователь (seam для других модулей) и проверка ролей."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ai_dnd.identity.mailer import Mailer, build_mailer
from ai_dnd.identity.models import UserRow
from ai_dnd.identity.oauth import OAuth, build_oauth
from ai_dnd.identity.sessions import SESSION_COOKIE, get_session_user
from ai_dnd.identity.settings import IdentitySettings, get_identity_settings
from ai_dnd.resources import Resources, get_resources


def get_settings_dep(request: Request) -> IdentitySettings:
    settings: IdentitySettings | None = getattr(request.app.state, "identity_settings", None)
    return settings if settings is not None else get_identity_settings()


def get_mailer_dep(request: Request) -> Mailer:
    mailer: Mailer | None = getattr(request.app.state, "identity_mailer", None)
    return mailer if mailer is not None else build_mailer(get_settings_dep(request))


def get_oauth_dep(request: Request) -> OAuth:
    oauth: OAuth | None = getattr(request.app.state, "identity_oauth", None)
    return oauth if oauth is not None else build_oauth(get_settings_dep(request))


async def get_current_user(
    request: Request,
    resources: Annotated[Resources, Depends(get_resources)],
) -> UserRow:
    """Cookie -> сессия -> пользователь; 401 при отсутствии, истечении, отзыве или бане."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated"}
        )
    async with resources.db_sessions() as db:
        user = await get_session_user(db, token)
    if user is None or user.banned_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated"}
        )
    return user


def require_role(*roles: str) -> Callable[..., Awaitable[UserRow]]:
    """Фабрика зависимостей: пропускает только пользователей с одной из ролей."""

    async def dependency(user: Annotated[UserRow, Depends(get_current_user)]) -> UserRow:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
        return user

    return dependency
