"""Общие ресурсы процесса api (база, Redis) и зависимости FastAPI для доступа к ним."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class Resources:
    db_engine: AsyncEngine
    db_sessions: async_sessionmaker[AsyncSession]
    redis: Redis


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


async def get_db_session(
    resources: Annotated[Resources, Depends(get_resources)],
) -> AsyncIterator[AsyncSession]:
    async with resources.db_sessions() as session:
        yield session
