"""Guard админ-роутеров поверх seam: свежий UserRow из базы (роль + бан).

realtime.CurrentUser роль не несёт, поэтому каждая админ-фабрика проверяет роль
по свежей строке users. Общий модуль для ядра (#27), надзора (#28) и операционки (#29).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from ai_dnd.identity.models import UserRow
from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.resources import Resources


def require_role(
    get_current_user: GetCurrentUser, *roles: str
) -> Callable[..., Awaitable[UserRow]]:
    """Фабрика зависимостей: пропуск только по свежей роли и без бана."""

    async def dependency(
        connection: HTTPConnection,
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> UserRow:
        resources: Resources = connection.app.state.resources
        async with resources.db_sessions() as db:
            row = await db.get(UserRow, user.user_id)
        if row is None or row.banned_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated"}
            )
        if row.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
        return row

    return dependency
