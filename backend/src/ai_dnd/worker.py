"""Брокер задач Taskiq. Запуск воркера:

uv run taskiq worker ai_dnd.worker:broker ai_dnd.tasks
"""

import structlog
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from ai_dnd.config import get_settings
from ai_dnd.log import configure_logging

RESULT_TTL_SECONDS = 3600

_settings = get_settings()

broker = RedisStreamBroker(url=str(_settings.redis_url)).with_result_backend(
    RedisAsyncResultBackend(redis_url=str(_settings.redis_url), result_ex_time=RESULT_TTL_SECONDS)
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _on_worker_startup(state: TaskiqState) -> None:
    configure_logging(_settings.log_level, _settings.log_format)
    structlog.get_logger(__name__).info("worker_started", environment=_settings.environment)
