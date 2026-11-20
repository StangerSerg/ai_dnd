"""Операционка админки (3.9): здоровье сервисов, watcher баланса, алерты, аналитика."""

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select, union_all
from sqlalchemy.orm import InstrumentedAttribute

from ai_dnd.admin import audit
from ai_dnd.admin.guard import require_role
from ai_dnd.ai import watcher
from ai_dnd.ai.models import LlmCall
from ai_dnd.config import get_settings
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.identity.csrf import enforce_csrf
from ai_dnd.identity.models import AuthSessionRow, UserRow
from ai_dnd.realtime.auth import GetCurrentUser
from ai_dnd.realtime.models import SeatRow
from ai_dnd.resources import Resources, get_resources
from ai_dnd.worker import broker

ResourcesDep = Annotated[Resources, Depends(get_resources)]


class ComponentHealth(BaseModel):
    status: str
    detail: str | None = None


class QueueHealth(BaseModel):
    status: str
    stream: str
    depth: int | None
    pending: int | None


class WorkerHealth(BaseModel):
    status: str
    note: str


class HealthResponse(BaseModel):
    status: str
    db: ComponentHealth
    redis: ComponentHealth
    queue: QueueHealth
    worker: WorkerHealth
    active_turn_windows: int | None
    last_ok_turn_at: datetime | None


class WatcherCheck(BaseModel):
    status: str
    checked_at: str
    balance_usd: Decimal | None
    low: bool
    message: str | None = None


class WatcherAlert(BaseModel):
    id: str
    at: str
    kind: str
    message: str


class WatcherResponse(BaseModel):
    last: WatcherCheck | None
    alerts: list[WatcherAlert]
    alert_usd: Decimal
    interval_s: int


class AckResponse(BaseModel):
    status: str
    alert_id: str


class AnalyticsDay(BaseModel):
    day: date
    ai_cost_usd: Decimal
    llm_calls: int
    llm_errors: int
    active_users: int
    active_sessions: int
    narrated_events: int


class AnalyticsResponse(BaseModel):
    days: int
    items: list[AnalyticsDay]


def _utc_day(column: InstrumentedAttribute[datetime]) -> Any:
    # timestamptz → UTC-дата: границы суток считаем по UTC, не по таймзоне сервера БД.
    return cast(func.timezone("UTC", column), Date)


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


async def _health(resources: Resources) -> HealthResponse:
    db_status = "ok"
    db_detail: str | None = None
    try:
        async with resources.db_sessions() as db:
            await db.scalar(select(1))
    except Exception as exc:
        db_status = "degraded"
        db_detail = str(exc)

    redis_status = "ok"
    redis_detail: str | None = None
    try:
        await resources.redis.ping()
    except Exception as exc:
        redis_status = "degraded"
        redis_detail = str(exc)

    queue_status = "degraded"
    depth: int | None = None
    pending: int | None = None
    if redis_status == "ok":
        try:
            # RedisStreamBroker хранит очередь в stream (XADD), не в list: глубина — XLEN.
            depth = int(await resources.redis.xlen(broker.queue_name))
            queue_status = "ok"
            try:
                info: Any = await resources.redis.xpending(
                    broker.queue_name, broker.consumer_group_name
                )
                pending = int(info["pending"])
            except Exception:
                # Группы нет (воркер ни разу не запускался) — pending неизвестен.
                pending = None
        except Exception:
            queue_status = "degraded"

    windows: int | None = None
    if redis_status == "ok":
        windows = 0
        # Паттерн ключей realtime.turns.window_key; только чтение, count у SCAN.
        async for _key in resources.redis.scan_iter(match="session:*:window", count=200):
            windows += 1

    last_ok_turn_at: datetime | None = None
    if db_status == "ok":
        async with resources.db_sessions() as db:
            last_ok_turn_at = await db.scalar(
                select(func.max(LlmCall.created_at)).where(LlmCall.status == "ok")
            )

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return HealthResponse(
        status=overall,
        db=ComponentHealth(status=db_status, detail=db_detail),
        redis=ComponentHealth(status=redis_status, detail=redis_detail),
        queue=QueueHealth(
            status=queue_status, stream=broker.queue_name, depth=depth, pending=pending
        ),
        worker=WorkerHealth(
            status="unknown",
            note="heartbeat воркера отсутствует; косвенно — queue.pending и last_ok_turn_at",
        ),
        active_turn_windows=windows,
        last_ok_turn_at=last_ok_turn_at,
    )


async def _analytics(resources: Resources, days: int) -> AnalyticsResponse:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    since = datetime.combine(start, time.min, tzinfo=UTC)

    async with resources.db_sessions() as db:
        llm_rows = (
            await db.execute(
                select(
                    _utc_day(LlmCall.created_at).label("day"),
                    func.coalesce(func.sum(LlmCall.cost), 0).label("cost"),
                    func.count().label("calls"),
                    func.count().filter(LlmCall.status == "error").label("errors"),
                )
                .where(LlmCall.created_at >= since)
                .group_by("day")
            )
        ).all()

        # Активный пользователь: вход (auth_sessions) или место за столом (seats) в периоде.
        auth_days = select(
            _utc_day(AuthSessionRow.created_at).label("day"),
            AuthSessionRow.user_id.label("uid"),
        ).where(AuthSessionRow.created_at >= since)
        seat_days = select(
            _utc_day(SeatRow.created_at).label("day"), SeatRow.user_id.label("uid")
        ).where(SeatRow.created_at >= since, SeatRow.user_id.is_not(None))
        users_sub = union_all(auth_days, seat_days).subquery()
        user_rows = (
            await db.execute(
                select(users_sub.c.day, func.count(func.distinct(users_sub.c.uid))).group_by(
                    users_sub.c.day
                )
            )
        ).all()

        session_rows = (
            await db.execute(
                select(_utc_day(GameSessionRow.updated_at).label("day"), func.count())
                .where(GameSessionRow.updated_at >= since)
                .group_by("day")
            )
        ).all()
        narrated_rows = (
            await db.execute(
                select(_utc_day(GameEventRow.created_at).label("day"), func.count())
                .where(GameEventRow.created_at >= since, GameEventRow.type == "Narrated")
                .group_by("day")
            )
        ).all()

    llm_by_day = {row.day: row for row in llm_rows}
    users_by_day = {row[0]: row[1] for row in user_rows}
    sessions_by_day = {row[0]: row[1] for row in session_rows}
    narrated_by_day = {row[0]: row[1] for row in narrated_rows}

    items: list[AnalyticsDay] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        llm = llm_by_day.get(day)
        items.append(
            AnalyticsDay(
                day=day,
                ai_cost_usd=Decimal(llm.cost) if llm is not None else Decimal(0),
                llm_calls=int(llm.calls) if llm is not None else 0,
                llm_errors=int(llm.errors) if llm is not None else 0,
                active_users=int(users_by_day.get(day, 0)),
                active_sessions=int(sessions_by_day.get(day, 0)),
                narrated_events=int(narrated_by_day.get(day, 0)),
            )
        )
    return AnalyticsResponse(days=days, items=items)


def build_ops_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(enforce_csrf)])

    admin_dep = require_role(get_current_user, "admin")
    AdminDep = Annotated[UserRow, Depends(admin_dep)]  # noqa: N806 — псевдоним зависимости

    @router.get("/ops/health")
    async def health(admin: AdminDep, resources: ResourcesDep) -> HealthResponse:
        return await _health(resources)

    @router.get("/ops/watcher")
    async def watcher_state(admin: AdminDep, resources: ResourcesDep) -> WatcherResponse:
        raw_last = await resources.redis.get(watcher.LAST_CHECK_KEY)
        last = WatcherCheck.model_validate(json.loads(raw_last)) if raw_last else None
        raw_alerts = await resources.redis.lrange(watcher.ALERTS_KEY, 0, watcher.ALERTS_LIMIT - 1)
        settings = watcher.get_watcher_settings()
        return WatcherResponse(
            last=last,
            alerts=[WatcherAlert.model_validate(json.loads(item)) for item in raw_alerts],
            alert_usd=settings.alert_usd,
            interval_s=settings.interval_s,
        )

    @router.post("/ops/watcher/run")
    async def watcher_run(admin: AdminDep, resources: ResourcesDep) -> WatcherCheck:
        server = get_settings()
        api_key = server.deepseek_api_key.get_secret_value() if server.deepseek_api_key else None
        settings = watcher.get_watcher_settings()
        check = await watcher.check_balance(resources.redis, api_key, settings.alert_usd)
        async with resources.db_sessions() as db, db.begin():
            await audit.record(
                db,
                admin.id,
                "ops.watcher_run",
                "ops_watcher",
                "deepseek",
                {"status": check["status"], "low": check["low"]},
            )
        return WatcherCheck.model_validate(check)

    @router.post("/ops/alerts/{alert_id}/ack")
    async def ack_alert(alert_id: str, admin: AdminDep, resources: ResourcesDep) -> AckResponse:
        raw_alerts = await resources.redis.lrange(watcher.ALERTS_KEY, 0, -1)
        target: tuple[Any, dict[str, Any]] | None = None
        for item in raw_alerts:
            data: dict[str, Any] = json.loads(item)
            if data.get("id") == alert_id:
                target = (item, data)
                break
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "alert_not_found"})
        item, data = target
        await resources.redis.lrem(watcher.ALERTS_KEY, 1, item)
        kind = data.get("kind")
        remaining = await resources.redis.lrange(watcher.ALERTS_KEY, 0, -1)
        same_kind_left = any(json.loads(entry).get("kind") == kind for entry in remaining)
        last_kind = _decode(await resources.redis.get(watcher.LAST_ALERT_KIND_KEY))
        if not same_kind_left and last_kind == kind:
            # Последний алерт этого kind ack'нут: новый чек снова может алертить.
            await resources.redis.delete(watcher.LAST_ALERT_KIND_KEY)
        async with resources.db_sessions() as db, db.begin():
            await audit.record(db, admin.id, "ops.alert_ack", "ops_alert", alert_id, {"kind": kind})
        return AckResponse(status="ok", alert_id=alert_id)

    @router.get("/ops/analytics")
    async def analytics(
        admin: AdminDep,
        resources: ResourcesDep,
        days: Annotated[int, Query(ge=1, le=90)] = 7,
    ) -> AnalyticsResponse:
        return await _analytics(resources, days)

    return router
