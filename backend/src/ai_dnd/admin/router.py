"""HTTP-ядро админки (3.9): пользователи, баны, биллинг-лимиты, предохранители, аудит.

Роутер собирается фабрикой по паттерну realtime/router.py: аутентификация — seam
``GetCurrentUser`` (realtime.auth). Роль и бан проверяются поверх seam по свежему
UserRow из базы: realtime.CurrentUser роль не несёт. Подключение — Root при wiring.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ai_dnd.admin import service
from ai_dnd.admin.guard import require_role
from ai_dnd.admin.models import SpendLimitRow
from ai_dnd.billing.ledger import CreditLedger
from ai_dnd.billing.settings import get_billing_settings
from ai_dnd.identity.csrf import enforce_csrf
from ai_dnd.identity.models import UserRow
from ai_dnd.identity.schemas import RoleUpdateRequest
from ai_dnd.realtime.auth import GetCurrentUser
from ai_dnd.resources import Resources, get_resources

ResourcesDep = Annotated[Resources, Depends(get_resources)]


class AdminUserItem(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str
    role: str
    banned_at: datetime | None
    created_at: datetime
    credits: int


class UsersResponse(BaseModel):
    users: list[AdminUserItem]
    total: int


class BanRequest(BaseModel):
    banned: bool


class ResetSessionsResponse(BaseModel):
    status: str
    revoked: int


class AdjustRequest(BaseModel):
    user_id: uuid.UUID
    credits: int
    reason: str = Field(min_length=1, max_length=500)


class AdjustResponse(BaseModel):
    user_id: uuid.UUID
    credits: int
    audit_id: uuid.UUID


class LimitItem(BaseModel):
    id: uuid.UUID
    scope: str
    scope_id: uuid.UUID
    daily_credits: int | None
    daily_usd: Decimal | None
    monthly_usd: Decimal | None
    updated_at: datetime


class LimitsDefaults(BaseModel):
    user_daily_spend_credits: int
    user_monthly_spend_credits: int
    session_daily_spend_credits: int
    global_daily_usd: Decimal
    global_monthly_usd: Decimal


class LimitsResponse(BaseModel):
    limits: list[LimitItem]
    defaults: LimitsDefaults


class LimitUpsertRequest(BaseModel):
    scope: Literal["user", "session", "global"]
    scope_id: uuid.UUID | None = None
    daily_credits: int | None = Field(default=None, ge=0)
    daily_usd: Decimal | None = Field(default=None, ge=0)
    monthly_usd: Decimal | None = Field(default=None, ge=0)


class StatusResponse(BaseModel):
    status: str


class FusesResponse(BaseModel):
    daily_usd_limit: Decimal
    monthly_usd_limit: Decimal
    daily_spent_usd: Decimal
    monthly_spent_usd: Decimal
    daily_tripped: bool
    monthly_tripped: bool


class AuditEntry(BaseModel):
    id: uuid.UUID
    at: datetime
    actor_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]


class AuditResponse(BaseModel):
    entries: list[AuditEntry]
    total: int


def _limit_item(row: SpendLimitRow) -> LimitItem:
    return LimitItem(
        id=row.id,
        scope=row.scope,
        scope_id=row.scope_id,
        daily_credits=row.daily_credits,
        daily_usd=row.daily_usd,
        monthly_usd=row.monthly_usd,
        updated_at=row.updated_at,
    )


async def _user_item(sessions: service.Sessions, user: UserRow) -> AdminUserItem:
    credits = await CreditLedger(sessions).balance(user.id)
    return AdminUserItem(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        banned_at=user.banned_at,
        created_at=user.created_at,
        credits=credits,
    )


def build_admin_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(enforce_csrf)])

    admin_dep = require_role(get_current_user, "admin")
    AdminDep = Annotated[UserRow, Depends(admin_dep)]  # noqa: N806 — псевдоним зависимости

    @router.get("/users")
    async def list_users(
        admin: AdminDep,
        resources: ResourcesDep,
        q: Annotated[str | None, Query(max_length=255)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> UsersResponse:
        rows, total = await service.list_users(resources.db_sessions, q, limit, offset)
        return UsersResponse(
            users=[
                AdminUserItem(
                    id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    role=user.role,
                    banned_at=user.banned_at,
                    created_at=user.created_at,
                    credits=credits,
                )
                for user, credits in rows
            ],
            total=total,
        )

    @router.post("/users/{user_id}/role")
    async def change_role(
        user_id: uuid.UUID,
        payload: RoleUpdateRequest,
        admin: AdminDep,
        resources: ResourcesDep,
    ) -> AdminUserItem:
        target = await service.change_role(resources.db_sessions, admin, user_id, payload.role)
        return await _user_item(resources.db_sessions, target)

    @router.post("/users/{user_id}/ban")
    async def ban_user(
        user_id: uuid.UUID,
        payload: BanRequest,
        admin: AdminDep,
        resources: ResourcesDep,
    ) -> AdminUserItem:
        target, _ = await service.set_ban(resources.db_sessions, admin, user_id, payload.banned)
        return await _user_item(resources.db_sessions, target)

    @router.post("/users/{user_id}/reset-sessions")
    async def reset_sessions(
        user_id: uuid.UUID,
        admin: AdminDep,
        resources: ResourcesDep,
    ) -> ResetSessionsResponse:
        revoked = await service.reset_sessions(resources.db_sessions, admin, user_id)
        return ResetSessionsResponse(status="ok", revoked=revoked)

    @router.post("/billing/adjust")
    async def adjust_credits(
        payload: AdjustRequest,
        admin: AdminDep,
        resources: ResourcesDep,
    ) -> AdjustResponse:
        balance, audit_id = await service.adjust_credits(
            resources.db_sessions, admin, payload.user_id, payload.credits, payload.reason
        )
        return AdjustResponse(user_id=payload.user_id, credits=balance, audit_id=audit_id)

    @router.get("/billing/limits")
    async def get_limits(admin: AdminDep, resources: ResourcesDep) -> LimitsResponse:
        rows = await service.list_limits(resources.db_sessions)
        cfg = get_billing_settings()
        return LimitsResponse(
            limits=[_limit_item(row) for row in rows],
            defaults=LimitsDefaults(
                user_daily_spend_credits=cfg.user_daily_spend_credits,
                user_monthly_spend_credits=cfg.user_monthly_spend_credits,
                session_daily_spend_credits=cfg.session_daily_spend_credits,
                global_daily_usd=cfg.global_daily_usd,
                global_monthly_usd=cfg.global_monthly_usd,
            ),
        )

    @router.put("/billing/limits")
    async def put_limit(
        payload: LimitUpsertRequest,
        admin: AdminDep,
        resources: ResourcesDep,
    ) -> LimitItem:
        row = await service.upsert_limit(
            resources.db_sessions,
            admin,
            payload.scope,
            payload.scope_id,
            payload.daily_credits,
            payload.daily_usd,
            payload.monthly_usd,
        )
        return _limit_item(row)

    @router.delete("/billing/limits")
    async def delete_limit(
        admin: AdminDep,
        resources: ResourcesDep,
        scope: Annotated[Literal["user", "session", "global"], Query()],
        scope_id: Annotated[uuid.UUID | None, Query()] = None,
    ) -> StatusResponse:
        await service.delete_limit(resources.db_sessions, admin, scope, scope_id)
        return StatusResponse(status="ok")

    @router.get("/billing/fuses")
    async def get_fuses(admin: AdminDep, resources: ResourcesDep) -> FusesResponse:
        report = await service.fuses(resources.db_sessions)
        return FusesResponse(**report)

    @router.get("/audit")
    async def get_audit(
        admin: AdminDep,
        resources: ResourcesDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        action: Annotated[str | None, Query(max_length=64)] = None,
        actor_id: Annotated[uuid.UUID | None, Query()] = None,
    ) -> AuditResponse:
        rows, total = await service.list_audit(
            resources.db_sessions, limit, offset, action, actor_id
        )
        return AuditResponse(
            entries=[
                AuditEntry(
                    id=row.id,
                    at=row.at,
                    actor_id=row.actor_id,
                    action=row.action,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    details=row.details,
                )
                for row in rows
            ],
            total=total,
        )

    return router
