"""Фоновые задачи. Модуль передаётся воркеру при запуске, чтобы он знал о задачах."""

import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.worker import broker

logger = structlog.get_logger(__name__)


async def _session_stopped(sessions: async_sessionmaker[AsyncSession], session_id: str) -> bool:
    """Сессия остановлена надзором: задачи ведущего молча пропускаются (no-op)."""
    from ai_dnd.game.models import GameSessionRow

    async with sessions() as db:
        status = await db.scalar(
            select(GameSessionRow.status).where(GameSessionRow.id == uuid.UUID(session_id))
        )
    return status is not None and status != "active"


@broker.task
async def ping(message: str = "ping") -> str:
    """Проверочная задача: воркер жив и забирает задачи из очереди."""
    logger.info("ping_received", message=message)
    return f"pong: {message}"


@broker.task
async def index_scenario_version(
    version_id: str, previous_version_id: str | None = None
) -> dict[str, int]:
    """Нарезать и проиндексировать версию сценария: эмбеддинги считаются здесь, в воркере."""
    from ai_dnd.config import get_settings
    from ai_dnd.db.engine import create_engine, create_session_factory
    from ai_dnd.rag.embeddings import Embedder
    from ai_dnd.rag.index import index_version

    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    embedder = Embedder(settings.embedding_model, settings.embedding_dim)
    previous = uuid.UUID(previous_version_id) if previous_version_id else None
    try:
        report = await index_version(
            sessions, uuid.UUID(version_id), embedder=embedder, previous_version_id=previous
        )
    finally:
        await engine.dispose()
    logger.info(
        "scenario_indexed",
        version_id=version_id,
        chunks=report.chunks,
        reused=report.reused,
        embedded=report.embedded,
    )
    return {
        "entities": report.entities,
        "chunks": report.chunks,
        "reused": report.reused,
        "embedded": report.embedded,
    }


@broker.task
async def dm_turn(
    session_id: str,
    actions: list[dict[str, str]],
    window_id: str,
    fake: bool | None = None,
) -> str:
    """Ход AI DM в веб-сессии: замок комнаты, GameMaster, биллинг, стриминг в комнату.

    Без замка — no-op (дубль задачи); повтор того же окна — no-op по маркеру.
    ``fake=None`` берёт AI_DND_FAKE_LLM из настроек оркестрации.
    """
    from redis.asyncio import Redis

    from ai_dnd.config import get_settings
    from ai_dnd.db.engine import create_engine, create_session_factory
    from ai_dnd.realtime.turns import resolve_fake, run_dm_turn

    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(str(settings.redis_url))
    try:
        if await _session_stopped(sessions, session_id):
            logger.info("session_stopped_skip", session_id=session_id)
            return "session_stopped"
        status = await run_dm_turn(
            sessions,
            redis,
            settings,
            uuid.UUID(session_id),
            actions,
            window_id,
            fake=resolve_fake(fake),
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    logger.info("dm_turn_done", session_id=session_id, window_id=window_id, status=status)
    return status


@broker.task
async def dm_open_session(session_id: str, fake: bool | None = None) -> str:
    """Первый рассказ AI DM при создании сессии с dm_mode="ai"; повтор — no-op."""
    from redis.asyncio import Redis

    from ai_dnd.config import get_settings
    from ai_dnd.db.engine import create_engine, create_session_factory
    from ai_dnd.realtime.turns import resolve_fake, run_dm_open

    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(str(settings.redis_url))
    try:
        if await _session_stopped(sessions, session_id):
            logger.info("session_stopped_skip", session_id=session_id)
            return "session_stopped"
        status = await run_dm_open(
            sessions, redis, settings, uuid.UUID(session_id), fake=resolve_fake(fake)
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    logger.info("dm_open_done", session_id=session_id, status=status)
    return status


@broker.task
async def combat_step(
    session_id: str,
    actor_id: str,
    option_index: int,
    fake: bool | None = None,
) -> str:
    """Боевой шаг игрока в ai-сессии: замок комнаты, действие движком, цикл боя.

    Без замка — no-op (дубль задачи); очередь и индекс перепроверяются под замком.
    ``fake=None`` берёт AI_DND_FAKE_LLM из настроек оркестрации.
    """
    from redis.asyncio import Redis

    from ai_dnd.config import get_settings
    from ai_dnd.db.engine import create_engine, create_session_factory
    from ai_dnd.realtime.turns import resolve_fake, run_combat_step

    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(str(settings.redis_url))
    try:
        if await _session_stopped(sessions, session_id):
            logger.info("session_stopped_skip", session_id=session_id)
            return "session_stopped"
        status = await run_combat_step(
            sessions,
            redis,
            settings,
            uuid.UUID(session_id),
            actor_id,
            option_index,
            fake=resolve_fake(fake),
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    logger.info(
        "combat_step_done",
        session_id=session_id,
        actor_id=actor_id,
        option_index=option_index,
        status=status,
    )
    return status


@broker.task
async def close_action_window(session_id: str, window_id: str, delay_seconds: float = 0.0) -> str:
    """Таймаут окна сбора действий: закрыть своё окно и поставить dm_turn.

    Устаревший window_id новое окно не трогает. RedisStreamBroker не умеет
    отложенную доставку, поэтому задержка выполняется сном внутри задачи;
    если комната занята ходом, закрытие перепланируется (замок снимется — ход
    закроет окно и сам, это страховка).
    """
    from redis.asyncio import Redis

    from ai_dnd.config import get_settings
    from ai_dnd.db.engine import create_engine, create_session_factory
    from ai_dnd.realtime.turns import get_turn_settings, try_close_and_dispatch

    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(str(settings.redis_url))
    try:
        status, _ = await try_close_and_dispatch(sessions, redis, uuid.UUID(session_id), window_id)
    finally:
        await redis.aclose()
        await engine.dispose()
    if status == "locked":
        retry = float(get_turn_settings().turn_close_retry_seconds)
        await close_action_window.kiq(session_id, window_id, retry)
    logger.info("action_window_closed", session_id=session_id, window_id=window_id, status=status)
    return status
