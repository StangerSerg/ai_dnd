"""Проверка здоровья: доступны ли база данных и Redis."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_dnd.resources import Resources, get_resources

CHECK_TIMEOUT_SECONDS = 2.0

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["system"])

HealthCheck = Callable[[], Awaitable[object]]
CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckStatus]


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis(client: Redis) -> None:
    await client.ping()


def get_health_checks(
    resources: Annotated[Resources, Depends(get_resources)],
) -> dict[str, HealthCheck]:
    return {
        "database": partial(check_database, resources.db_engine),
        "redis": partial(check_redis, resources.redis),
    }


async def _run_check(name: str, check: HealthCheck) -> CheckStatus:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception:
        # Подробности — только в лог, наружу отдаём лишь статус.
        logger.exception("health_check_failed", check=name)
        return "error"
    return "ok"


@router.get("/health")
async def health(
    response: Response,
    checks: Annotated[dict[str, HealthCheck], Depends(get_health_checks)],
) -> HealthResponse:
    names = list(checks)
    results = await asyncio.gather(*(_run_check(name, checks[name]) for name in names))
    statuses = dict(zip(names, results, strict=True))

    overall: CheckStatus = "ok" if all(s == "ok" for s in statuses.values()) else "error"
    if overall == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status=overall, checks=statuses)
