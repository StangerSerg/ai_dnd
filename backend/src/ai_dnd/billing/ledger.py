"""Операции журнала кредитов: резерв, списание по факту, возврат, ежедневный грант.

Конкурентность — advisory-блокировка на пользователя (``pg_advisory_xact_lock`` от
хэша user_id) внутри транзакции. Выбрана вместо SELECT ... FOR UPDATE по строке
users: журнал не зависит от модели users (её создаёт параллельная задача), блокировка
сама снимается при коммите/откате и сериализует все операции по одному пользователю.

Инварианты: резерв по ref один; charge по ref один (двойное списание невозможно);
возвраты не превышают резерв; после расчёта сумма записей по ref равна -actual.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from ai_dnd.billing.errors import BillingStop, InsufficientCredits, LedgerError
from ai_dnd.billing.limits import day_start, effective_user_daily_credits, month_start
from ai_dnd.billing.models import CreditLedgerRow
from ai_dnd.billing.settings import BillingSettings, get_billing_settings

# Записи этих причин в сумме дают расход: резерв -R, списание -actual, возврат +R.
SPEND_REASONS = ("reserve", "charge", "refund")
# Ручная корректировка админом: влияет на баланс, но НЕ считается расходом (лимиты AI).
ADJUST_REASON = "admin_adjust"


@dataclass(frozen=True, slots=True)
class LedgerRef:
    """Ссылка операции на внешний объект, например llm_call."""

    ref_type: str
    ref_id: str


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": str(user_id)},
    )


async def _balance(session: AsyncSession, user_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerRow.delta), 0)).where(
            CreditLedgerRow.user_id == user_id
        )
    )
    return total if total is not None else 0


async def user_spend(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    """Расход кредитов с момента ``since``: минус сумма записей reserve/charge/refund.

    Незакрытые резервы считаются расходом — это консервативно и безопасно для лимитов.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerRow.delta), 0)).where(
            CreditLedgerRow.user_id == user_id,
            CreditLedgerRow.reason.in_(SPEND_REASONS),
            CreditLedgerRow.created_at >= since,
        )
    )
    return -(total if total is not None else 0)


async def balance_of(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Баланс в открытой сессии: для админ-операций в одной транзакции с аудитом."""
    return await _balance(session, user_id)


async def admin_adjust(
    session: AsyncSession, user_id: uuid.UUID, credits: int, audit_id: uuid.UUID
) -> None:
    """Запись ручной корректировки (знаковые кредиты != 0), ref — id строки аудита.

    Вызывается в транзакции вызывающего (под блокировкой пользователя); reason
    ``admin_adjust`` входит в баланс, но не в SPEND_REASONS — лимиты AI его не видят.
    """
    if credits == 0:
        raise LedgerError("adjust amount must not be zero")
    await _lock_user(session, user_id)
    session.add(
        CreditLedgerRow(
            user_id=user_id,
            delta=credits,
            reason=ADJUST_REASON,
            ref_type="audit",
            ref_id=str(audit_id),
        )
    )


def _ref_filter(ref: LedgerRef) -> tuple[ColumnElement[bool], ...]:
    return (
        CreditLedgerRow.ref_type == ref.ref_type,
        CreditLedgerRow.ref_id == ref.ref_id,
    )


class CreditLedger:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: BillingSettings | None = None,
    ) -> None:
        self._sessions = sessions
        self._settings = settings or get_billing_settings()

    async def balance(self, user_id: uuid.UUID) -> int:
        async with self._sessions() as session:
            return await _balance(session, user_id)

    async def reserve(
        self,
        user_id: uuid.UUID,
        amount: int,
        ref: LedgerRef,
        *,
        daily_limit: int | None = None,
        monthly_limit: int | None = None,
    ) -> None:
        """Зарезервировать кредиты перед AI-вызовом: запись -amount.

        Баланс и лимиты пользователя проверяются под блокировкой в одной транзакции:
        конкурентные резервы не уводят баланс в минус и не проскакивают лимит.
        """
        if amount <= 0:
            raise LedgerError("reserve amount must be positive")
        async with self._sessions() as session, session.begin():
            await _lock_user(session, user_id)
            existing = await session.scalar(select(CreditLedgerRow.id).where(*_ref_filter(ref)))
            if existing is not None:
                raise LedgerError(f"ref already used: {ref.ref_type}/{ref.ref_id}")
            balance = await _balance(session, user_id)
            if balance < amount:
                raise InsufficientCredits(balance, amount)
            now = datetime.now(UTC)
            # User-override daily_credits читается в этой же транзакции под блокировкой.
            daily = await effective_user_daily_credits(session, user_id, daily_limit)
            if daily is not None:
                spent = await user_spend(session, user_id, day_start(now))
                if spent + amount > daily:
                    raise BillingStop(
                        "user_daily_limit",
                        f"Дневной лимит кредитов исчерпан ({spent} из {daily}). "
                        "ИИ отдыхает до завтра; игра между людьми продолжается.",
                    )
            if monthly_limit is not None:
                spent = await user_spend(session, user_id, month_start(now))
                if spent + amount > monthly_limit:
                    raise BillingStop(
                        "user_monthly_limit",
                        f"Месячный лимит кредитов исчерпан ({spent} из {monthly_limit}). "
                        "ИИ отдыхает до следующего месяца; игра между людьми продолжается.",
                    )
            session.add(
                CreditLedgerRow(
                    user_id=user_id,
                    delta=-amount,
                    reason="reserve",
                    ref_type=ref.ref_type,
                    ref_id=ref.ref_id,
                )
            )

    async def charge(self, ref: LedgerRef, actual: int) -> None:
        """Списать фактическую стоимость вызова: запись -actual. Повторно — нельзя."""
        if actual < 0:
            raise LedgerError("charge amount must not be negative")
        async with self._sessions() as session, session.begin():
            reserve_row = await self._reserve_row(session, ref)
            await _lock_user(session, reserve_row.user_id)
            await self._ensure_not_charged(session, ref)
            session.add(
                CreditLedgerRow(
                    user_id=reserve_row.user_id,
                    delta=-actual,
                    reason="charge",
                    ref_type=ref.ref_type,
                    ref_id=ref.ref_id,
                )
            )

    async def refund(self, ref: LedgerRef, difference: int) -> None:
        """Вернуть кредиты по резерву: запись +difference, суммарно не больше резерва."""
        if difference <= 0:
            raise LedgerError("refund amount must be positive")
        async with self._sessions() as session, session.begin():
            reserve_row = await self._reserve_row(session, ref)
            await _lock_user(session, reserve_row.user_id)
            await self._ensure_refund_room(session, ref, -reserve_row.delta, difference)
            session.add(
                CreditLedgerRow(
                    user_id=reserve_row.user_id,
                    delta=difference,
                    reason="refund",
                    ref_type=ref.ref_type,
                    ref_id=ref.ref_id,
                )
            )

    async def settle(self, ref: LedgerRef, actual: int) -> None:
        """Расчёт по факту одной транзакцией: charge -actual и возврат всего резерва +R.

        Итог по ref: -R - actual + R = -actual — игрок платит ровно фактическую стоимость.
        Если факт больше резерва, баланс может уйти в минус: следующий резерв не пройдёт.
        """
        if actual < 0:
            raise LedgerError("settle amount must not be negative")
        async with self._sessions() as session, session.begin():
            reserve_row = await self._reserve_row(session, ref)
            await _lock_user(session, reserve_row.user_id)
            await self._ensure_not_charged(session, ref)
            session.add(
                CreditLedgerRow(
                    user_id=reserve_row.user_id,
                    delta=-actual,
                    reason="charge",
                    ref_type=ref.ref_type,
                    ref_id=ref.ref_id,
                )
            )
            session.add(
                CreditLedgerRow(
                    user_id=reserve_row.user_id,
                    delta=-reserve_row.delta,
                    reason="refund",
                    ref_type=ref.ref_type,
                    ref_id=ref.ref_id,
                )
            )

    async def grant_daily_free(self, user_id: uuid.UUID, amount: int | None = None) -> bool:
        """Бесплатные кредиты раз в сутки (UTC) на пользователя; идемпотентно."""
        credits = self._settings.daily_free_credits if amount is None else amount
        if credits <= 0:
            return False
        today = datetime.now(UTC).date().isoformat()
        async with self._sessions() as session, session.begin():
            await _lock_user(session, user_id)
            existing = await session.scalar(
                select(CreditLedgerRow.id).where(
                    CreditLedgerRow.user_id == user_id,
                    CreditLedgerRow.reason == "daily_free",
                    CreditLedgerRow.ref_id == today,
                )
            )
            if existing is not None:
                return False
            session.add(
                CreditLedgerRow(
                    user_id=user_id,
                    delta=credits,
                    reason="daily_free",
                    ref_type="daily_free",
                    ref_id=today,
                )
            )
        return True

    async def ref_total(self, ref: LedgerRef) -> int:
        """Сумма записей по ref: после расчёта должна равняться -actual."""
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(CreditLedgerRow.delta), 0)).where(*_ref_filter(ref))
            )
            return total if total is not None else 0

    # --- Внутреннее (вызывается под блокировкой пользователя) ---

    async def _reserve_row(self, session: AsyncSession, ref: LedgerRef) -> CreditLedgerRow:
        row = await session.scalar(
            select(CreditLedgerRow).where(*_ref_filter(ref), CreditLedgerRow.reason == "reserve")
        )
        if row is None:
            raise LedgerError(f"no reserve for ref: {ref.ref_type}/{ref.ref_id}")
        return row

    async def _ensure_not_charged(self, session: AsyncSession, ref: LedgerRef) -> None:
        existing = await session.scalar(
            select(CreditLedgerRow.id).where(*_ref_filter(ref), CreditLedgerRow.reason == "charge")
        )
        if existing is not None:
            raise LedgerError(f"ref already charged: {ref.ref_type}/{ref.ref_id}")

    async def _ensure_refund_room(
        self, session: AsyncSession, ref: LedgerRef, reserved: int, difference: int
    ) -> None:
        refunded = await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerRow.delta), 0)).where(
                *_ref_filter(ref), CreditLedgerRow.reason == "refund"
            )
        )
        if (refunded if refunded is not None else 0) + difference > reserved:
            raise LedgerError(f"refund exceeds reserve: {ref.ref_type}/{ref.ref_id}")
