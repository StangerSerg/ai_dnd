"""Приложение FastAPI. Запуск для разработки: uv run uvicorn ai_dnd.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from ai_dnd.config import Settings, get_settings
from ai_dnd.db.engine import create_engine, create_session_factory
from ai_dnd.health import router as health_router
from ai_dnd.log import configure_logging
from ai_dnd.resources import Resources

logger = structlog.get_logger(__name__)


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
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("api_stopped")

    app = FastAPI(title="AI DnD Portal", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
