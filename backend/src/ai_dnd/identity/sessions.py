"""Серверные сессии: случайный токен в httpOnly-cookie, в базе только sha256."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import Response
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_dnd.config import Environment
from ai_dnd.identity.models import AuthSessionRow, UserRow
from ai_dnd.identity.settings import IdentitySettings

SESSION_COOKIE = "ai_dnd_session"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(
    db: AsyncSession, user_id: uuid.UUID, ttl_days: int
) -> tuple[AuthSessionRow, str]:
    """Создаёт сессию (flush без commit) и возвращает строку и сырой токен для cookie."""
    token = secrets.token_urlsafe(32)
    row = AuthSessionRow(
        user_id=user_id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )
    db.add(row)
    await db.flush()
    return row, token


async def get_session_user(db: AsyncSession, token: str) -> UserRow | None:
    stmt = (
        select(UserRow)
        .join(AuthSessionRow, AuthSessionRow.user_id == UserRow.id)
        .where(
            AuthSessionRow.token_hash == hash_token(token),
            AuthSessionRow.revoked_at.is_(None),
            AuthSessionRow.expires_at > func.now(),
            # Бан закрывает и cookie-вход через realtime-seam (HTTP и WebSocket).
            UserRow.banned_at.is_(None),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def revoke_session(db: AsyncSession, token: str) -> None:
    stmt = (
        update(AuthSessionRow)
        .where(AuthSessionRow.token_hash == hash_token(token), AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await db.execute(stmt)


async def revoke_all_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Отзывает все активные сессии пользователя (бан, сброс сессий); flush без commit."""
    stmt = (
        update(AuthSessionRow)
        .where(AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    return result.rowcount


def set_session_cookie(response: Response, token: str, settings: IdentitySettings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.environment != Environment.DEV,
        path="/",
    )


def clear_session_cookie(response: Response, settings: IdentitySettings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.environment != Environment.DEV,
    )
