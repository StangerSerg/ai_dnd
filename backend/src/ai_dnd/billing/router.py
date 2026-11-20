"""HTTP-ручки биллинга: баланс пользователя и админ-отчёт расходов (подэтап 3.6).

Роутер самодостаточный: main.py не меняется, подключение — при wiring.
Auth — заглушка-seam ``get_current_user`` (по умолчанию 501): Root заменит на identity
через dependency_overrides или свою зависимость; роль admin проверяется поверх неё.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from ai_dnd.ai.models import LlmCall
from ai_dnd.billing.ledger import CreditLedger
from ai_dnd.billing.limits import day_start
from ai_dnd.billing.models import CreditLedgerRow
from ai_dnd.billing.settings import credits_to_usd, get_billing_settings
from ai_dnd.resources import Resources, get_resources


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    is_admin: bool = False


async def get_current_user() -> CurrentUser:
    """Заглушка до подключения identity (подэтап 3.1). Seam для замены Root."""
    raise HTTPException(
        status_code=501, detail="Аутентификация ещё не подключена (seam для identity)."
    )


async def require_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Нужна роль администратора.")
    return user


class BalanceResponse(BaseModel):
    user_id: uuid.UUID
    credits: int
    usd: Decimal


class DayExpense(BaseModel):
    day: date
    calls: int
    cost_usd: Decimal
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


class TaskExpense(BaseModel):
    task: str
    calls: int
    cost_usd: Decimal


class UserExpense(BaseModel):
    user_id: uuid.UUID | None
    calls: int
    cost_usd: Decimal


class LedgerSummary(BaseModel):
    reason: str
    credits: int
    usd: Decimal


class ExpensesResponse(BaseModel):
    since: datetime
    days: list[DayExpense]
    tasks: list[TaskExpense]
    users: list[UserExpense]
    ledger: list[LedgerSummary]


router = APIRouter(tags=["billing"])


@router.get("/api/billing/balance")
async def get_balance(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> BalanceResponse:
    ledger = CreditLedger(resources.db_sessions)
    credits = await ledger.balance(user.id)
    return BalanceResponse(
        user_id=user.id,
        credits=credits,
        usd=credits_to_usd(credits, get_billing_settings().credit_usd),
    )


@router.get("/api/admin/expenses")
async def get_expenses(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    resources: Annotated[Resources, Depends(get_resources)],
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> ExpensesResponse:
    since = day_start(datetime.now(UTC)) - timedelta(days=days - 1)
    credit_usd = get_billing_settings().credit_usd
    day_col = func.date_trunc("day", LlmCall.created_at).label("day")
    async with resources.db_sessions() as session:
        day_rows = (
            await session.execute(
                select(
                    day_col,
                    func.count(),
                    func.coalesce(func.sum(LlmCall.cost), 0),
                    func.coalesce(func.sum(LlmCall.input_tokens), 0),
                    func.coalesce(func.sum(LlmCall.cached_input_tokens), 0),
                    func.coalesce(func.sum(LlmCall.output_tokens), 0),
                )
                .where(LlmCall.created_at >= since)
                .group_by(day_col)
                .order_by(day_col)
            )
        ).all()
        task_rows = (
            await session.execute(
                select(LlmCall.task, func.count(), func.coalesce(func.sum(LlmCall.cost), 0))
                .where(LlmCall.created_at >= since)
                .group_by(LlmCall.task)
                .order_by(LlmCall.task)
            )
        ).all()
        user_rows = (
            await session.execute(
                select(LlmCall.user_id, func.count(), func.coalesce(func.sum(LlmCall.cost), 0))
                .where(LlmCall.created_at >= since)
                .group_by(LlmCall.user_id)
            )
        ).all()
        ledger_rows = (
            await session.execute(
                select(
                    CreditLedgerRow.reason,
                    func.coalesce(func.sum(CreditLedgerRow.delta), 0),
                )
                .where(CreditLedgerRow.created_at >= since)
                .group_by(CreditLedgerRow.reason)
                .order_by(CreditLedgerRow.reason)
            )
        ).all()
    return ExpensesResponse(
        since=since,
        days=[
            DayExpense(
                day=day.date(),
                calls=int(calls),
                cost_usd=Decimal(str(cost)),
                input_tokens=int(inp),
                cached_input_tokens=int(cached),
                output_tokens=int(out),
            )
            for day, calls, cost, inp, cached, out in day_rows
        ],
        tasks=[
            TaskExpense(task=task, calls=int(calls), cost_usd=Decimal(str(cost)))
            for task, calls, cost in task_rows
        ],
        users=[
            UserExpense(user_id=user_id, calls=int(calls), cost_usd=Decimal(str(cost)))
            for user_id, calls, cost in user_rows
        ],
        ledger=[
            LedgerSummary(
                reason=reason, credits=int(credits), usd=credits_to_usd(int(credits), credit_usd)
            )
            for reason, credits in ledger_rows
        ],
    )
