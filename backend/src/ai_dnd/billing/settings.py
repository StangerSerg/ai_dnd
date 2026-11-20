"""Настройки биллинга: курс кредитов, лимиты и предохранители (архитектура, 11.1).

Курс: 1 кредит = 0.0001 USD, то есть 10 000 кредитов = $1. Ход AI DM стоит доли цента
(замеры этапа 1), значит десятки кредитов. Курс меняется конфигурацией — интерфейс
пользователя не зависит от цен провайдера напрямую.

Отдельный класс настроек с env-префиксом AI_DND_BILLING_ (вложен в общий AI_DND_):
общий Settings не меняется, подключение — при wiring. Все деньги — Decimal.
"""

from decimal import ROUND_CEILING, Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BillingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_DND_BILLING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Курс: сколько долларов стоит один кредит.
    credit_usd: Decimal = Decimal("0.0001")
    # Ежедневные бесплатные кредиты: 100 000 = $10. Щедрые дефолты для альфа-
    # тестирования: тестировщики не платят, кредиты не должны блокировать игру.
    daily_free_credits: int = 100000
    # Резерв на один AI-вызов: 50 кредитов = $0.005, с запасом к средней цене хода.
    reserve_credits: int = 50
    # Лимиты расхода кредитов: пользователь в день/месяц, сессия в день.
    # Щедрые альфа-значения: $10/день и $100/месяц на пользователя, $10/день на сессию.
    user_daily_spend_credits: int = 100000
    user_monthly_spend_credits: int = 1000000
    session_daily_spend_credits: int = 100000
    # Глобальные предохранители платформы по фактическим расходам (llm_calls.cost):
    # остаются защитой реального баланса провайдера, потолок поднят для альфы.
    global_daily_usd: Decimal = Decimal("20.00")
    global_monthly_usd: Decimal = Decimal("300.00")
    # Порог уведомления админу о низком балансе у провайдера.
    provider_balance_alert_usd: Decimal = Decimal("2.00")


@lru_cache
def get_billing_settings() -> BillingSettings:
    """Настройки биллинга, прочитанные один раз за время жизни процесса."""
    return BillingSettings()


def usd_to_credits(usd: Decimal, credit_usd: Decimal) -> int:
    """Доллары в кредиты, округление вверх: платформа не теряет доли кредита."""
    if usd <= 0:
        return 0
    return int((usd / credit_usd).to_integral_value(rounding=ROUND_CEILING))


def credits_to_usd(credits: int, credit_usd: Decimal) -> Decimal:
    return Decimal(credits) * credit_usd
