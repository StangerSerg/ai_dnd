"""Лимиты расходов и глобальные предохранители платформы (архитектура, 11.1).

Источник фактических расходов — ``llm_calls.cost`` (USD Decimal); расход кредитов
пользователя считается по ``credit_ledger`` (см. ledger.user_spend). Проверки —
до AI-вызова: предохранитель отключает только ИИ, игра между людьми продолжается.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.admin.models import ZERO_UUID, SpendLimitRow
from ai_dnd.ai.models import LlmCall
from ai_dnd.billing.errors import BillingStop
from ai_dnd.billing.settings import BillingSettings, get_billing_settings, usd_to_credits


def _utc_now() -> datetime:
    return datetime.now(UTC)


def day_start(now: datetime) -> datetime:
    """Начало суток UTC: все дневные границы считаются по UTC."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime) -> datetime:
    return day_start(now).replace(day=1)


async def llm_cost_since(
    session: AsyncSession, since: datetime, session_id: uuid.UUID | None = None
) -> Decimal:
    """Суммарная фактическая стоимость вызовов с момента ``since`` (вся платформа или сессия)."""
    stmt = select(func.coalesce(func.sum(LlmCall.cost), 0)).where(LlmCall.created_at >= since)
    if session_id is not None:
        stmt = stmt.where(LlmCall.session_id == session_id)
    cost = await session.scalar(stmt)
    return cost if cost is not None else Decimal(0)


async def _limit_row(
    session: AsyncSession, scope: str, scope_id: uuid.UUID
) -> SpendLimitRow | None:
    return await session.scalar(
        select(SpendLimitRow).where(
            SpendLimitRow.scope == scope, SpendLimitRow.scope_id == scope_id
        )
    )


async def effective_user_daily_credits(
    session: AsyncSession, user_id: uuid.UUID, default: int | None
) -> int | None:
    """User-override daily_credits поверх env; null-поле = env-дефолт.

    Вызывается внутри транзакции резерва, чтобы override читался атомарно с проверкой.
    """
    row = await _limit_row(session, "user", user_id)
    if row is not None and row.daily_credits is not None:
        return row.daily_credits
    return default


async def effective_session_daily_credits(
    session: AsyncSession, session_id: uuid.UUID, default: int
) -> int:
    """Session-override daily_credits поверх env session_daily_spend_credits."""
    row = await _limit_row(session, "session", session_id)
    if row is not None and row.daily_credits is not None:
        return row.daily_credits
    return default


async def effective_global_usd(
    session: AsyncSession, settings: BillingSettings
) -> tuple[Decimal, Decimal]:
    """Global-override (daily_usd, monthly_usd) поверх env-предохранителей."""
    row = await _limit_row(session, "global", ZERO_UUID)
    daily = settings.global_daily_usd
    monthly = settings.global_monthly_usd
    if row is not None:
        if row.daily_usd is not None:
            daily = row.daily_usd
        if row.monthly_usd is not None:
            monthly = row.monthly_usd
    return daily, monthly


class BillingLimits:
    """Глобальный предохранитель и лимит сессии. Лимиты пользователя проверяются
    атомарно вместе с резервом (CreditLedger.reserve), чтобы конкурентные резервы
    не проскочили мимо лимита."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: BillingSettings | None = None,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sessions = sessions
        self._settings = settings or get_billing_settings()
        self._now = now

    async def check_global(self) -> None:
        now = self._now()
        async with self._sessions() as session:
            daily_limit, monthly_limit = await effective_global_usd(session, self._settings)
            day = await llm_cost_since(session, day_start(now))
            if day >= daily_limit:
                raise BillingStop(
                    "global_daily_fuse",
                    "Сработал дневной предохранитель расходов платформы: ИИ временно отключён. "
                    "Игра между людьми продолжается.",
                )
            month = await llm_cost_since(session, month_start(now))
            if month >= monthly_limit:
                raise BillingStop(
                    "global_monthly_fuse",
                    "Сработал месячный предохранитель расходов платформы: ИИ временно отключён. "
                    "Игра между людьми продолжается.",
                )

    async def check_session(self, session_id: uuid.UUID | None) -> None:
        if session_id is None:
            return
        async with self._sessions() as session:
            limit = await effective_session_daily_credits(
                session, session_id, self._settings.session_daily_spend_credits
            )
            cost = await llm_cost_since(session, day_start(self._now()), session_id)
        spent = usd_to_credits(cost, self._settings.credit_usd)
        if spent >= limit:
            raise BillingStop(
                "session_daily_limit",
                f"Дневной лимит кредитов сессии исчерпан ({spent} из "
                f"{limit}). ИИ в этой сессии отдыхает "
                "до завтра; игра между людьми продолжается.",
            )
