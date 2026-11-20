"""Слежение за балансом провайдера и уведомление админа (архитектура, 11.1).

Bounded decision: баланс DeepSeek запрашивается через API с ключом; внешний API без
ключа не дёргаем. Заложен seam — протокол BalanceWatcher; заглушка
DisabledBalanceWatcher возвращает None (проверка выключена). Реальный watcher
и канал уведомлений (Telegram/почта) подключаются при wiring, без изменения ядра.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Protocol

from ai_dnd.billing.settings import BillingSettings

Notify = Callable[[str], Awaitable[None]]


class BalanceWatcher(Protocol):
    async def fetch_balance_usd(self) -> Decimal | None:
        """Баланс у провайдера в USD; None — проверка недоступна/выключена."""
        ...


class DisabledBalanceWatcher:
    """Заглушка до появления ключа и канала уведомлений."""

    async def fetch_balance_usd(self) -> Decimal | None:
        return None


async def check_provider_balance(
    watcher: BalanceWatcher, settings: BillingSettings, notify: Notify
) -> bool:
    """Уведомить админа, если баланс ниже порога. True — уведомление отправлено."""
    balance = await watcher.fetch_balance_usd()
    if balance is None or balance >= settings.provider_balance_alert_usd:
        return False
    await notify(
        f"Баланс провайдера LLM низкий: ${balance} "
        f"(порог ${settings.provider_balance_alert_usd}). Пополните баланс вручную."
    )
    return True
