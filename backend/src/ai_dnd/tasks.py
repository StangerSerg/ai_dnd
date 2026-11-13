"""Фоновые задачи. Модуль передаётся воркеру при запуске, чтобы он знал о задачах."""

import structlog

from ai_dnd.worker import broker

logger = structlog.get_logger(__name__)


@broker.task
async def ping(message: str = "ping") -> str:
    """Проверочная задача: воркер жив и забирает задачи из очереди."""
    logger.info("ping_received", message=message)
    return f"pong: {message}"
