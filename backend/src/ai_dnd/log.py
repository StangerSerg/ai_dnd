"""Настройка логов: structlog и стандартный logging (uvicorn и др.) в одном формате."""

import logging

import structlog
from structlog.typing import Processor

from ai_dnd.config import LogFormat, LogLevel


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
        final_processors: list[Processor] = [structlog.processors.dict_tracebacks, renderer]
    else:
        renderer = structlog.dev.ConsoleRenderer()
        final_processors = [renderer]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final_processors],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn ставит свои обработчики — убираем их, чтобы всё шло через общий формат.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
