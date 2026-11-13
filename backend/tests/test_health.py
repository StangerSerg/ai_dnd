import asyncio

import httpx2 as httpx
import pytest
from fastapi import FastAPI

from ai_dnd import health
from ai_dnd.health import HealthCheck, get_health_checks
from ai_dnd.main import create_app


async def ok() -> None:
    return None


async def fail() -> None:
    raise ConnectionError("service is down")


async def hang() -> None:
    await asyncio.sleep(10)


def make_app(checks: dict[str, HealthCheck]) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_health_checks] = lambda: checks
    return app


async def get_health(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


async def test_ok_when_all_checks_pass() -> None:
    response = await get_health(make_app({"database": ok, "redis": ok}))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_503_when_a_check_fails() -> None:
    response = await get_health(make_app({"database": ok, "redis": fail}))

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"database": "ok", "redis": "error"}}


async def test_hanging_check_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)

    response = await get_health(make_app({"database": hang}))

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error"}
