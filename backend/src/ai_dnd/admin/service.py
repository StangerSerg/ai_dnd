"""Бизнес-логика ядра админки (3.9): пользователи, баны, корректировки, лимиты, аудит.

Каждая мутация пишет строку аудита в той же транзакции (admin.audit.record).
Ошибки — HTTPException с кодом в detail, как в identity.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.admin import audit
from ai_dnd.admin.models import ZERO_UUID, AuditLogRow, SpendLimitRow
from ai_dnd.billing.ledger import CreditLedger, admin_adjust, balance_of
from ai_dnd.billing.limits import day_start, effective_global_usd, llm_cost_since, month_start
from ai_dnd.billing.settings import BillingSettings, get_billing_settings
from ai_dnd.identity.models import UserRow
from ai_dnd.identity.sessions import revoke_all_user_sessions

Sessions = async_sessionmaker[AsyncSession]

SCOPES = ("user", "session", "global")


async def _target_user(db: AsyncSession, user_id: uuid.UUID) -> UserRow:
    target = await db.get(UserRow, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "user_not_found"}
        )
    return target


async def list_users(
    sessions: Sessions, q: str | None, limit: int, offset: int
) -> tuple[list[tuple[UserRow, int]], int]:
    """Страница пользователей с балансом кредитов (через CreditLedger) и total."""
    cond = None
    if q:
        pattern = f"%{q}%"
        cond = or_(UserRow.email.ilike(pattern), UserRow.display_name.ilike(pattern))
    async with sessions() as db:
        count_stmt = select(func.count()).select_from(UserRow)
        stmt = select(UserRow).order_by(UserRow.created_at.desc()).limit(limit).offset(offset)
        if cond is not None:
            count_stmt = count_stmt.where(cond)
            stmt = stmt.where(cond)
        total = await db.scalar(count_stmt)
        rows = list((await db.execute(stmt)).scalars())
    ledger = CreditLedger(sessions)
    result = [(row, await ledger.balance(row.id)) for row in rows]
    return result, total if total is not None else 0


async def change_role(sessions: Sessions, actor: UserRow, user_id: uuid.UUID, role: str) -> UserRow:
    if user_id == actor.id:
        raise HTTPException(
            status_code=422,
            detail={"code": "cannot_change_self"},
        )
    async with sessions() as db, db.begin():
        target = await _target_user(db, user_id)
        old = target.role
        target.role = role
        await audit.record(
            db,
            actor.id,
            "user.role_change",
            "user",
            str(user_id),
            {"old": old, "new": role},
        )
    return target


async def set_ban(
    sessions: Sessions, actor: UserRow, user_id: uuid.UUID, banned: bool
) -> tuple[UserRow, int]:
    """Бан: banned_at=now + отзыв всех auth_sessions; разбан: banned_at=None."""
    async with sessions() as db, db.begin():
        target = await _target_user(db, user_id)
        revoked = 0
        if banned:
            target.banned_at = datetime.now(UTC)
            revoked = await revoke_all_user_sessions(db, user_id)
        else:
            target.banned_at = None
        await audit.record(
            db,
            actor.id,
            "user.ban" if banned else "user.unban",
            "user",
            str(user_id),
            {"banned": banned, "revoked_sessions": revoked},
        )
    return target, revoked


async def reset_sessions(sessions: Sessions, actor: UserRow, user_id: uuid.UUID) -> int:
    async with sessions() as db, db.begin():
        await _target_user(db, user_id)
        revoked = await revoke_all_user_sessions(db, user_id)
        await audit.record(
            db,
            actor.id,
            "user.reset_sessions",
            "user",
            str(user_id),
            {"revoked_sessions": revoked},
        )
    return revoked


async def adjust_credits(
    sessions: Sessions, actor: UserRow, user_id: uuid.UUID, credits: int, reason: str
) -> tuple[int, uuid.UUID]:
    """Ручная корректировка баланса; ref_id записи ledger = id строки аудита.

    Возвращает (новый баланс, id аудита). Аудит и запись журнала — одна транзакция.
    """
    if credits == 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_amount"},
        )
    async with sessions() as db, db.begin():
        await _target_user(db, user_id)
        audit_id = await audit.record(
            db,
            actor.id,
            "billing.adjust",
            "user",
            str(user_id),
            {"credits": credits, "reason": reason},
        )
        await admin_adjust(db, user_id, credits, audit_id)
        balance = await balance_of(db, user_id)
    return balance, audit_id


async def list_limits(sessions: Sessions) -> list[SpendLimitRow]:
    async with sessions() as db:
        stmt = select(SpendLimitRow).order_by(SpendLimitRow.scope, SpendLimitRow.scope_id)
        return list((await db.execute(stmt)).scalars())


def _validate_limit(
    scope: str,
    scope_id: uuid.UUID | None,
    daily_credits: int | None,
    daily_usd: Decimal | None,
    monthly_usd: Decimal | None,
) -> uuid.UUID:
    """global — только usd-поля (scope_id=ZERO_UUID); user/session — только daily_credits."""
    if scope not in SCOPES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_scope"},
        )
    if scope == "global":
        if daily_credits is not None:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_limit_fields"},
            )
        return ZERO_UUID
    if daily_usd is not None or monthly_usd is not None:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_limit_fields"},
        )
    if scope_id is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "scope_id_required"},
        )
    return scope_id


def _limit_details(row: SpendLimitRow) -> dict[str, Any]:
    return {
        "scope": row.scope,
        "scope_id": str(row.scope_id),
        "daily_credits": row.daily_credits,
        "daily_usd": None if row.daily_usd is None else str(row.daily_usd),
        "monthly_usd": None if row.monthly_usd is None else str(row.monthly_usd),
    }


async def upsert_limit(
    sessions: Sessions,
    actor: UserRow,
    scope: str,
    scope_id: uuid.UUID | None,
    daily_credits: int | None,
    daily_usd: Decimal | None,
    monthly_usd: Decimal | None,
) -> SpendLimitRow:
    """Upsert по (scope, scope_id); null-поля сбрасываются к env-дефолтам."""
    sid = _validate_limit(scope, scope_id, daily_credits, daily_usd, monthly_usd)
    async with sessions() as db, db.begin():
        stmt = pg_insert(SpendLimitRow).values(
            scope=scope,
            scope_id=sid,
            daily_credits=daily_credits,
            daily_usd=daily_usd,
            monthly_usd=monthly_usd,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["scope", "scope_id"],
            set_={
                "daily_credits": stmt.excluded.daily_credits,
                "daily_usd": stmt.excluded.daily_usd,
                "monthly_usd": stmt.excluded.monthly_usd,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)
        row = await db.scalar(
            select(SpendLimitRow).where(SpendLimitRow.scope == scope, SpendLimitRow.scope_id == sid)
        )
        if row is None:  # pragma: no cover — upsert гарантирует строку
            raise RuntimeError("spend limit row missing after upsert")
        await audit.record(
            db,
            actor.id,
            "billing.limit_upsert",
            "spend_limit",
            f"{scope}:{sid}",
            _limit_details(row),
        )
    return row


async def delete_limit(
    sessions: Sessions, actor: UserRow, scope: str, scope_id: uuid.UUID | None
) -> None:
    """Удаление строки = полный сброс к env-дефолтам."""
    sid = _validate_limit(scope, scope_id, None, None, None)
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(SpendLimitRow).where(SpendLimitRow.scope == scope, SpendLimitRow.scope_id == sid)
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail={"code": "limit_not_found"}
            )
        details = _limit_details(row)
        await db.execute(delete(SpendLimitRow).where(SpendLimitRow.id == row.id))
        await audit.record(
            db, actor.id, "billing.limit_delete", "spend_limit", f"{scope}:{sid}", details
        )


async def fuses(sessions: Sessions, settings: BillingSettings | None = None) -> dict[str, Any]:
    """Эффективные глобальные предохранители + фактические расходы и статус."""
    cfg = settings if settings is not None else get_billing_settings()
    now = datetime.now(UTC)
    async with sessions() as db:
        daily_limit, monthly_limit = await effective_global_usd(db, cfg)
        daily_spent = await llm_cost_since(db, day_start(now))
        monthly_spent = await llm_cost_since(db, month_start(now))
    return {
        "daily_usd_limit": daily_limit,
        "monthly_usd_limit": monthly_limit,
        "daily_spent_usd": daily_spent,
        "monthly_spent_usd": monthly_spent,
        "daily_tripped": daily_spent >= daily_limit,
        "monthly_tripped": monthly_spent >= monthly_limit,
    }


async def list_audit(
    sessions: Sessions,
    limit: int,
    offset: int,
    action: str | None,
    actor_id: uuid.UUID | None,
) -> tuple[list[AuditLogRow], int]:
    async with sessions() as db:
        count_stmt = select(func.count()).select_from(AuditLogRow)
        stmt = select(AuditLogRow).order_by(AuditLogRow.at.desc()).limit(limit).offset(offset)
        if action is not None:
            count_stmt = count_stmt.where(AuditLogRow.action == action)
            stmt = stmt.where(AuditLogRow.action == action)
        if actor_id is not None:
            count_stmt = count_stmt.where(AuditLogRow.actor_id == actor_id)
            stmt = stmt.where(AuditLogRow.actor_id == actor_id)
        total = await db.scalar(count_stmt)
        rows = list((await db.execute(stmt)).scalars())
    return rows, total if total is not None else 0
