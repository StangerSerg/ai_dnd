"""Приложение FastAPI. Запуск для разработки: uv run uvicorn ai_dnd.main:app --reload"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Request
from redis.asyncio import Redis

from ai_dnd.admin.ops import build_ops_router
from ai_dnd.admin.router import build_admin_router
from ai_dnd.admin.supervision import build_supervision_router
from ai_dnd.ai import watcher
from ai_dnd.billing import router as billing_router
from ai_dnd.characters.router import build_characters_router
from ai_dnd.config import Settings, get_settings
from ai_dnd.db.engine import create_engine, create_session_factory
from ai_dnd.health import router as health_router
from ai_dnd.identity.deps import get_current_user as identity_current_user
from ai_dnd.identity.router import install_identity
from ai_dnd.log import configure_logging
from ai_dnd.realtime.auth import identity_get_current_user
from ai_dnd.realtime.router import build_router as build_sessions_router
from ai_dnd.realtime.turns import build_turns_router
from ai_dnd.realtime.ws import build_ws_router
from ai_dnd.resources import Resources, get_resources
from ai_dnd.scenarios.router import build_scenarios_router

logger = structlog.get_logger(__name__)


async def _billing_current_user(
    request: Request,
    resources: Annotated[Resources, Depends(get_resources)],
) -> billing_router.CurrentUser:
    user = await identity_current_user(request, resources)
    return billing_router.CurrentUser(id=user.id, is_admin=user.role == "admin")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(str(settings.redis_url))
        app.state.resources = Resources(
            db_engine=engine,
            db_sessions=create_session_factory(engine),
            redis=redis,
        )
        logger.info("api_started", environment=settings.environment)
        watcher_task: asyncio.Task[None] | None = None
        watcher_settings = watcher.get_watcher_settings()
        api_key = (
            settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else None
        )
        if watcher_settings.enabled and api_key:
            watcher_task = asyncio.create_task(
                watcher.watcher_loop(
                    app.state.resources.db_sessions, redis, api_key, watcher_settings
                ),
                name="balance-watcher",
            )
            logger.info("watcher_started", interval_s=watcher_settings.interval_s)
        try:
            yield
        finally:
            if watcher_task is not None:
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task
            await redis.aclose()
            await engine.dispose()
            logger.info("api_stopped")

    app = FastAPI(title="AI DnD Portal", lifespan=lifespan)
    app.include_router(health_router)
    install_identity(app)
    app.include_router(build_sessions_router(identity_get_current_user))
    app.include_router(build_ws_router(identity_get_current_user))
    app.include_router(build_turns_router(identity_get_current_user))
    app.include_router(build_characters_router(identity_get_current_user))
    app.include_router(build_scenarios_router(identity_get_current_user))
    app.include_router(build_admin_router(identity_get_current_user))
    app.include_router(build_supervision_router(identity_get_current_user))
    app.include_router(build_ops_router(identity_get_current_user))
    app.include_router(billing_router.router)
    app.dependency_overrides[billing_router.get_current_user] = _billing_current_user
    return app


app = create_app()
