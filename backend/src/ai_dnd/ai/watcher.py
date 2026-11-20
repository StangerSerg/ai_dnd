"""Watcher баланса DeepSeek: периодический чек и алерты в Redis только для админки."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.ai.providers.deepseek import fetch_balance

logger = structlog.get_logger(__name__)

ALERTS_KEY = "ops:alerts"
LAST_CHECK_KEY = "ops:watcher:last"
LAST_ALERT_KIND_KEY = "ops:watcher:last_alert_kind"
ALERTS_LIMIT = 100

Sessions = async_sessionmaker[AsyncSession]


class WatcherSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_DND_WATCHER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Порог алерта о низком балансе провайдера, USD.
    alert_usd: Decimal = Decimal("5.0")
    interval_s: int = 300
    # Фоновый цикл включается явно: смоки/тесты с create_app() не должны ходить в сеть.
    enabled: bool = False


def get_watcher_settings() -> WatcherSettings:
    # Без lru_cache: смоки и dev меняют порог через env между запусками.
    return WatcherSettings()


async def fetch_deepseek_balance(api_key: str) -> Decimal | None:
    """Баланс в USD или None: сеть/401/битый ответ не роняют watcher, причина — в логе."""
    try:
        return await fetch_balance(api_key)
    except Exception as exc:
        logger.warning("deepseek_balance_fetch_failed", reason=str(exc))
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


async def _push_alert(redis: Redis, kind: str, message: str, at: str) -> None:
    # Идемпотентность: подряд одинаковый kind не дублируем, пока прошлый не ack'нут.
    if _text(await redis.get(LAST_ALERT_KIND_KEY)) == kind:
        return
    alert = {"id": uuid.uuid4().hex, "at": at, "kind": kind, "message": message}
    await redis.lpush(ALERTS_KEY, json.dumps(alert, ensure_ascii=False))
    await redis.ltrim(ALERTS_KEY, 0, ALERTS_LIMIT - 1)
    await redis.set(LAST_ALERT_KIND_KEY, kind)


async def check_balance(
    redis: Redis, api_key: str | None, threshold_usd: Decimal
) -> dict[str, Any]:
    """Один чек баланса: результат в ops:watcher:last, алерт при low/ошибке.

    Без ключа — честный статус unconfigured (баланс не выдумывается и не алертится).
    """
    checked_at = datetime.now(UTC).isoformat()
    check: dict[str, Any] = {
        "status": "unconfigured",
        "checked_at": checked_at,
        "balance_usd": None,
        "low": False,
        "message": None,
    }
    if api_key:
        balance = await fetch_deepseek_balance(api_key)
        if balance is None:
            check.update(
                status="error",
                message="баланс DeepSeek недоступен (сеть или ключ), подробности в логе",
            )
            await _push_alert(redis, "error", str(check["message"]), checked_at)
        else:
            low = balance < threshold_usd
            check.update(status="ok", balance_usd=balance, low=low)
            if low:
                message = f"баланс DeepSeek {balance} USD ниже порога {threshold_usd} USD"
                check["message"] = message
                await _push_alert(redis, "low_balance", message, checked_at)
            else:
                # Баланс восстановился: следующий low снова имеет право на алерт.
                check["message"] = None
                await redis.delete(LAST_ALERT_KIND_KEY)
    # Decimal сериализуем строкой: JSON-число потеряло бы точность денег.
    await redis.set(LAST_CHECK_KEY, json.dumps(check, ensure_ascii=False, default=str))
    return check


async def watcher_loop(
    sessions: Sessions, redis: Redis, api_key: str | None, settings: WatcherSettings
) -> None:
    """Бесконечный цикл чеков; запускает Root в lifespan. CancelledError пробрасывается.

    ``sessions`` зарезервирован для будущих проверок БД: чек баланса использует только Redis.
    """
    while True:
        try:
            await check_balance(redis, api_key, settings.alert_usd)
        except Exception:
            logger.exception("watcher_check_failed")
        await asyncio.sleep(settings.interval_s)
