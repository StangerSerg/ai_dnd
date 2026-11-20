"""Аутентификация realtime: seam для identity (подэтап 3.1 подключит Root при приёмке).

Роутер и WebSocket-шлюз узнают пользователя только через зависимость ``GetCurrentUser``;
своя аутентификация здесь не делается. Заглушка запрещает доступ, пока identity нет.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, status
from starlette.requests import HTTPConnection

from ai_dnd.identity.sessions import SESSION_COOKIE, get_session_user


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: uuid.UUID
    display_name: str = ""


# Единый seam для HTTP и WebSocket: оба соединения — HTTPConnection.
GetCurrentUser = Callable[[HTTPConnection], Awaitable[CurrentUser]]


async def stub_get_current_user(connection: HTTPConnection) -> CurrentUser:
    """Заглушка до подключения identity: доступ запрещён."""
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")


async def identity_get_current_user(connection: HTTPConnection) -> CurrentUser:
    """Cookie сессии identity -> пользователь; работает и для HTTP, и для WebSocket."""
    token = connection.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    resources = connection.app.state.resources
    async with resources.db_sessions() as db:
        user = await get_session_user(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    return CurrentUser(user_id=user.id, display_name=user.display_name)
