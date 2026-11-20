"""Биллинг: кредиты, лимиты и защита бюджета платформы (подэтап 3.6, архитектура 11.1).

Кредит — внутренняя единица учёта (курс задаётся в BillingSettings). Журнал
``credit_ledger`` хранит все начисления и списания; баланс — сумма записей.
BillingGuard оборачивает только AI-вызовы: при исчерпании лимитов ИИ останавливается
с понятным сообщением (BillingStop), а игра между людьми продолжается.
"""

from ai_dnd.billing.errors import BillingStop, InsufficientCredits, LedgerError
from ai_dnd.billing.guard import BillingGuard
from ai_dnd.billing.ledger import CreditLedger, LedgerRef
from ai_dnd.billing.limits import BillingLimits
from ai_dnd.billing.models import CreditLedgerRow
from ai_dnd.billing.settings import (
    BillingSettings,
    credits_to_usd,
    get_billing_settings,
    usd_to_credits,
)
from ai_dnd.billing.watcher import BalanceWatcher, DisabledBalanceWatcher, check_provider_balance

__all__ = [
    "BalanceWatcher",
    "BillingGuard",
    "BillingLimits",
    "BillingSettings",
    "BillingStop",
    "CreditLedger",
    "CreditLedgerRow",
    "DisabledBalanceWatcher",
    "InsufficientCredits",
    "LedgerError",
    "LedgerRef",
    "check_provider_balance",
    "credits_to_usd",
    "get_billing_settings",
    "usd_to_credits",
]
