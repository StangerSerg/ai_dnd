"""Надзор админки (3.9): сессии, видимость сценариев, очередь жалоб, аудит модерации."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ai_dnd.admin import audit
from ai_dnd.admin.guard import require_role
from ai_dnd.admin.models import ComplaintRow
from ai_dnd.game.events import Narrated
from ai_dnd.game.journal import DbJournal
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.identity.csrf import enforce_csrf
from ai_dnd.identity.models import UserRow
from ai_dnd.realtime.auth import GetCurrentUser
from ai_dnd.realtime.broadcast import BroadcastJournal, publish_session_stopped
from ai_dnd.realtime.models import SeatRow
from ai_dnd.realtime.schemas import EventsResponse
from ai_dnd.realtime.service import event_out
from ai_dnd.resources import Resources, get_resources
from ai_dnd.scenarios.models import ScenarioRow, ScenarioVersionRow

ResourcesDep = Annotated[Resources, Depends(get_resources)]


class SessionItem(BaseModel):
    id: uuid.UUID
    scenario: str
    language: str
    dm_mode: str
    status: str
    seats_count: int
    last_seq: int
    created_at: datetime
    host_name: str | None


class SessionsResponse(BaseModel):
    sessions: list[SessionItem]
    total: int


class SeatDetail(BaseModel):
    id: uuid.UUID
    role: str
    actor_id: str
    name: str | None
    muted: bool
    user_email: str | None


class SessionDetail(BaseModel):
    id: uuid.UUID
    scenario: str
    language: str
    dm_mode: str
    status: str
    last_seq: int
    setup: dict[str, Any]
    state: dict[str, Any]
    seats: list[SeatDetail]


class StopRequest(BaseModel):
    stopped: bool


class StopResponse(BaseModel):
    session_id: uuid.UUID
    status: str


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MessageResponse(BaseModel):
    session_id: uuid.UUID
    seq: int


class ScenarioItem(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    language: str
    visibility: str
    status: str
    current_version_number: int | None
    author_name: str | None


class ScenariosResponse(BaseModel):
    scenarios: list[ScenarioItem]
    total: int


class VisibilityRequest(BaseModel):
    visibility: Literal["private", "unlisted", "public"]


class ComplaintItem(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    session_scenario: str
    reporter_name: str
    target_type: str
    target_id: str
    reason: str
    status: str
    created_at: datetime
    resolved_by_name: str | None
    resolved_at: datetime | None
    resolution: str | None


class ComplaintsResponse(BaseModel):
    complaints: list[ComplaintItem]
    total: int


class ResolveRequest(BaseModel):
    status: Literal["resolved", "dismissed"]
    resolution: str = Field(min_length=1, max_length=500)


async def _session_row(db: AsyncSession, session_id: uuid.UUID) -> GameSessionRow:
    row = await db.get(GameSessionRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return row


def _complaint_item(
    row: ComplaintRow, scenario: str, reporter_name: str, resolver_name: str | None
) -> ComplaintItem:
    return ComplaintItem(
        id=row.id,
        session_id=row.session_id,
        session_scenario=scenario,
        reporter_name=reporter_name,
        target_type=row.target_type,
        target_id=row.target_id,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
        resolved_by_name=resolver_name,
        resolved_at=row.resolved_at,
        resolution=row.resolution,
    )


def build_supervision_router(get_current_user: GetCurrentUser) -> APIRouter:
    """Надзор: сессии — только admin; сценарии и жалобы — admin+moderator."""
    router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(enforce_csrf)])

    admin_dep = require_role(get_current_user, "admin")
    AdminDep = Annotated[UserRow, Depends(admin_dep)]  # noqa: N806 — псевдоним зависимости
    mod_dep = require_role(get_current_user, "admin", "moderator")
    ModDep = Annotated[UserRow, Depends(mod_dep)]  # noqa: N806 — псевдоним зависимости

    @router.get("/sessions")
    async def list_sessions(
        admin: AdminDep,
        resources: ResourcesDep,
        status: Annotated[Literal["active", "stopped"] | None, Query()] = None,
        q: Annotated[str | None, Query(max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SessionsResponse:
        seats_count = (
            select(func.count())
            .select_from(SeatRow)
            .where(SeatRow.session_id == GameSessionRow.id)
            .correlate(GameSessionRow)
            .scalar_subquery()
        )
        conditions = []
        if status is not None:
            conditions.append(GameSessionRow.status == status)
        if q:
            conditions.append(GameSessionRow.scenario.ilike(f"%{q}%"))
        async with resources.db_sessions() as db:
            count_stmt = select(func.count()).select_from(GameSessionRow)
            stmt = (
                select(GameSessionRow, seats_count, UserRow.display_name)
                .outerjoin(UserRow, UserRow.id == GameSessionRow.host_id)
                .order_by(GameSessionRow.created_at.desc(), GameSessionRow.id)
                .limit(limit)
                .offset(offset)
            )
            for cond in conditions:
                count_stmt = count_stmt.where(cond)
                stmt = stmt.where(cond)
            total = await db.scalar(count_stmt)
            rows = (await db.execute(stmt)).all()
        return SessionsResponse(
            sessions=[
                SessionItem(
                    id=row.id,
                    scenario=row.scenario,
                    language=row.language,
                    dm_mode=row.dm_mode,
                    status=row.status,
                    seats_count=int(count or 0),
                    last_seq=row.last_seq,
                    created_at=row.created_at,
                    host_name=host_name,
                )
                for row, count, host_name in rows
            ],
            total=total if total is not None else 0,
        )

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: uuid.UUID, admin: AdminDep, resources: ResourcesDep
    ) -> SessionDetail:
        """Полный надзорный снимок: setup и state как есть, включая dm facts/summary."""
        async with resources.db_sessions() as db:
            row = await _session_row(db, session_id)
            seat_rows = (
                await db.execute(
                    select(SeatRow, UserRow.email)
                    .outerjoin(UserRow, UserRow.id == SeatRow.user_id)
                    .where(SeatRow.session_id == session_id)
                    .order_by(SeatRow.created_at)
                )
            ).all()
        setup = SessionSetup.model_validate(row.setup)
        seats = []
        for seat, email in seat_rows:
            player = setup.seat(seat.actor_id)
            seats.append(
                SeatDetail(
                    id=seat.id,
                    role=seat.role,
                    actor_id=seat.actor_id,
                    name=player.name if player is not None else None,
                    muted=seat.muted,
                    user_email=email,
                )
            )
        return SessionDetail(
            id=row.id,
            scenario=row.scenario,
            language=row.language,
            dm_mode=row.dm_mode,
            status=row.status,
            last_seq=row.last_seq,
            setup=row.setup,
            state=row.state,
            seats=seats,
        )

    @router.get("/sessions/{session_id}/events")
    async def get_session_events(
        session_id: uuid.UUID,
        admin: AdminDep,
        resources: ResourcesDep,
        since: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> EventsResponse:
        """Журнал без фильтра видимости: надзор видит и dm-события."""
        async with resources.db_sessions() as db:
            row = await _session_row(db, session_id)
            events = list(
                (
                    await db.scalars(
                        select(GameEventRow)
                        .where(GameEventRow.session_id == session_id, GameEventRow.seq > since)
                        .order_by(GameEventRow.seq)
                        .limit(limit)
                    )
                ).all()
            )
        return EventsResponse(events=[event_out(item) for item in events], last_seq=row.last_seq)

    @router.post("/sessions/{session_id}/stop")
    async def stop_session(
        session_id: uuid.UUID, body: StopRequest, admin: AdminDep, resources: ResourcesDep
    ) -> StopResponse:
        async with resources.db_sessions() as db, db.begin():
            row = await _session_row(db, session_id)
            row.status = "stopped" if body.stopped else "active"
            await audit.record(
                db,
                admin.id,
                "session.stop" if body.stopped else "session.resume",
                "session",
                str(session_id),
                {"stopped": body.stopped},
            )
        await publish_session_stopped(resources.redis, session_id, stopped=body.stopped)
        return StopResponse(session_id=session_id, status=row.status)

    @router.post("/sessions/{session_id}/message")
    async def post_session_message(
        session_id: uuid.UUID, body: MessageRequest, admin: AdminDep, resources: ResourcesDep
    ) -> MessageResponse:
        """Вмешательство админа: публичный голос DM в журнал и комнату."""
        sessions = resources.db_sessions
        async with sessions() as db:
            row = await _session_row(db, session_id)
        event = Narrated(text=body.text)
        state = apply_events(SessionState.model_validate(row.state), [event])
        journal = BroadcastJournal(DbJournal(sessions, session_id), resources.redis, sessions)
        await journal.append([event], state)
        async with sessions() as db:
            last_seq = await db.scalar(
                select(GameSessionRow.last_seq).where(GameSessionRow.id == session_id)
            )
        # DbJournal коммитит свою транзакцию сам, поэтому аудит пишется следом отдельно:
        # рассказ уже доставлен, потеря аудита не откатывает журнал (долг прототипа).
        async with sessions() as db, db.begin():
            await audit.record(
                db, admin.id, "session.message", "session", str(session_id), {"text": body.text}
            )
        return MessageResponse(session_id=session_id, seq=int(last_seq or 0))

    @router.get("/scenarios")
    async def list_scenarios(
        moderator: ModDep,
        resources: ResourcesDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ScenariosResponse:
        async with resources.db_sessions() as db:
            total = await db.scalar(select(func.count()).select_from(ScenarioRow))
            rows = (
                await db.execute(
                    select(ScenarioRow, ScenarioVersionRow.number, UserRow.display_name)
                    .outerjoin(
                        ScenarioVersionRow, ScenarioVersionRow.id == ScenarioRow.current_version_id
                    )
                    .outerjoin(UserRow, UserRow.id == ScenarioRow.author_id)
                    .order_by(ScenarioRow.created_at.desc(), ScenarioRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        return ScenariosResponse(
            scenarios=[
                ScenarioItem(
                    id=row.id,
                    slug=row.slug,
                    title=row.title,
                    language=row.language,
                    visibility=row.visibility,
                    status=row.status,
                    current_version_number=number,
                    author_name=author_name,
                )
                for row, number, author_name in rows
            ],
            total=total if total is not None else 0,
        )

    @router.post("/scenarios/{scenario_id}/visibility")
    async def change_visibility(
        scenario_id: uuid.UUID, body: VisibilityRequest, moderator: ModDep, resources: ResourcesDep
    ) -> ScenarioItem:
        async with resources.db_sessions() as db, db.begin():
            row = await db.get(ScenarioRow, scenario_id)
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "scenario_not_found"})
            old = row.visibility
            row.visibility = body.visibility
            await audit.record(
                db,
                moderator.id,
                "scenario.visibility",
                "scenario",
                str(scenario_id),
                {"old": old, "new": body.visibility},
            )
            number: int | None = None
            if row.current_version_id is not None:
                number = await db.scalar(
                    select(ScenarioVersionRow.number).where(
                        ScenarioVersionRow.id == row.current_version_id
                    )
                )
            author_name: str | None = None
            if row.author_id is not None:
                author_name = await db.scalar(
                    select(UserRow.display_name).where(UserRow.id == row.author_id)
                )
        return ScenarioItem(
            id=row.id,
            slug=row.slug,
            title=row.title,
            language=row.language,
            visibility=row.visibility,
            status=row.status,
            current_version_number=number,
            author_name=author_name,
        )

    @router.get("/complaints")
    async def list_complaints(
        moderator: ModDep,
        resources: ResourcesDep,
        status: Annotated[Literal["open", "resolved", "dismissed"] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ComplaintsResponse:
        reporter = aliased(UserRow)
        resolver = aliased(UserRow)
        async with resources.db_sessions() as db:
            count_stmt = select(func.count()).select_from(ComplaintRow)
            stmt = (
                select(
                    ComplaintRow,
                    GameSessionRow.scenario,
                    reporter.display_name,
                    resolver.display_name,
                )
                .join(GameSessionRow, GameSessionRow.id == ComplaintRow.session_id)
                .join(reporter, reporter.id == ComplaintRow.reporter_id)
                .outerjoin(resolver, resolver.id == ComplaintRow.resolved_by)
                .order_by(ComplaintRow.created_at.desc(), ComplaintRow.id)
                .limit(limit)
                .offset(offset)
            )
            if status is not None:
                count_stmt = count_stmt.where(ComplaintRow.status == status)
                stmt = stmt.where(ComplaintRow.status == status)
            total = await db.scalar(count_stmt)
            rows = (await db.execute(stmt)).all()
        return ComplaintsResponse(
            complaints=[
                _complaint_item(row, scenario, reporter_name, resolver_name)
                for row, scenario, reporter_name, resolver_name in rows
            ],
            total=total if total is not None else 0,
        )

    @router.post("/complaints/{complaint_id}/resolve")
    async def resolve_complaint(
        complaint_id: uuid.UUID, body: ResolveRequest, moderator: ModDep, resources: ResourcesDep
    ) -> ComplaintItem:
        async with resources.db_sessions() as db, db.begin():
            row = await db.scalar(
                select(ComplaintRow).where(ComplaintRow.id == complaint_id).with_for_update()
            )
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "complaint_not_found"})
            if row.status != "open":
                raise HTTPException(status_code=409, detail={"code": "complaint_closed"})
            row.status = body.status
            row.resolution = body.resolution
            row.resolved_by = moderator.id
            row.resolved_at = datetime.now(UTC)
            await audit.record(
                db,
                moderator.id,
                "complaint.resolve",
                "complaint",
                str(complaint_id),
                {"status": body.status, "resolution": body.resolution},
            )
            scenario = await db.scalar(
                select(GameSessionRow.scenario).where(GameSessionRow.id == row.session_id)
            )
            reporter_name = await db.scalar(
                select(UserRow.display_name).where(UserRow.id == row.reporter_id)
            )
        return _complaint_item(
            row, str(scenario or ""), str(reporter_name or ""), moderator.display_name
        )

    return router
