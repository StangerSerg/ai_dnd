"""API игровых сессий: создание, вход по приглашению, состояние и журнал (подэтап 3.3).

Роутер собирается фабрикой: аутентификация — зависимость-seam ``GetCurrentUser``
(см. ``realtime.auth``), которую Root заменит на identity при приёмке.
"""

import uuid
from collections.abc import Iterable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ai_dnd.admin.models import ComplaintRow
from ai_dnd.game.models import GameSessionRow
from ai_dnd.game.session import SessionSetup
from ai_dnd.identity.models import UserRow
from ai_dnd.realtime import service
from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.realtime.models import SeatRow
from ai_dnd.realtime.schemas import (
    ChatHistoryResponse,
    ChatMessageOut,
    ChatRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    EventsResponse,
    JoinRequest,
    JoinResponse,
    KickRequest,
    KickResponse,
    MuteRequest,
    MuteResponse,
    OpenTableItem,
    RecruitingRequest,
    RecruitingResponse,
    SessionPreviewResponse,
    SessionResponse,
    StopSessionResponse,
    TransferHostRequest,
    TransferHostResponse,
)
from ai_dnd.resources import Resources, get_resources
from ai_dnd.scenarios.models import ScenarioRow, ScenarioVersionRow


class SessionListItem(BaseModel):
    """Сессия в списке «мои игры»: роль спрашивающего за столом."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    scenario: str
    scenario_title: str | None = None
    language: str
    dm_mode: str
    status: str
    last_seq: int
    role: str
    seats_total: int | None = None
    seats_taken: int = 0
    host_name: str | None = None
    invite_token: str | None = None


class ComplaintRequest(BaseModel):
    """Жалоба участника стола на сессию, событие журнала или пользователя."""

    model_config = ConfigDict(extra="forbid")

    target_type: Literal["session", "event", "user"]
    target_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class ComplaintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: str


class RestartSessionResponse(BaseModel):
    """Рестарт стола хостом: журнал и состояние сброшены, opening генерируется заново."""

    model_config = ConfigDict(extra="forbid")

    restarted: bool = True
    state_version: int


def build_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    @router.get("", response_model=list[SessionListItem])
    async def list_sessions(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> list[SessionListItem]:
        async with resources.db_sessions() as db:
            rows = (
                await db.execute(
                    select(GameSessionRow, SeatRow.role)
                    .join(SeatRow, SeatRow.session_id == GameSessionRow.id)
                    .where(SeatRow.user_id == user.user_id)
                    .order_by(GameSessionRow.created_at.desc())
                )
            ).all()
            session_ids = [row.id for row, _ in rows]
            # Зал (#32) показывает название/места/хозяина и для «моих» столов вне
            # открытого набора — без запомненных invite и дополнительных превью.
            titles: dict[uuid.UUID, str] = {}
            version_ids = [
                row.scenario_version_id for row, _ in rows if row.scenario_version_id is not None
            ]
            if version_ids:
                for version_id, title in (
                    await db.execute(
                        select(ScenarioVersionRow.id, ScenarioRow.title)
                        .join(ScenarioRow, ScenarioRow.id == ScenarioVersionRow.scenario_id)
                        .where(ScenarioVersionRow.id.in_(version_ids))
                    )
                ).all():
                    titles[version_id] = title
            players: dict[uuid.UUID, int] = {}
            if session_ids:
                for seat_session_id, seat_role in (
                    await db.execute(
                        select(SeatRow.session_id, SeatRow.role).where(
                            SeatRow.session_id.in_(session_ids)
                        )
                    )
                ).all():
                    if seat_role == "player":
                        players[seat_session_id] = players.get(seat_session_id, 0) + 1
            hosts: dict[uuid.UUID, str] = {}
            host_ids = [row.host_id for row, _ in rows if row.host_id is not None]
            if host_ids:
                for host_id, display_name in (
                    await db.execute(
                        select(UserRow.id, UserRow.display_name).where(UserRow.id.in_(host_ids))
                    )
                ).all():
                    hosts[host_id] = display_name
        return [
            SessionListItem(
                id=row.id,
                scenario=row.scenario,
                scenario_title=titles.get(row.scenario_version_id)
                if row.scenario_version_id is not None
                else None,
                language=row.language,
                dm_mode=row.dm_mode,
                status=row.status,
                last_seq=row.last_seq,
                role=role,
                seats_total=row.seats_total,
                seats_taken=players.get(row.id, 0),
                host_name=hosts.get(row.host_id) if row.host_id is not None else None,
                invite_token=row.invite_token,
            )
            for row, role in rows
        ]

    @router.post("/", response_model=CreateSessionResponse, status_code=201)
    async def create_session(
        body: CreateSessionRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CreateSessionResponse:
        try:
            session_id, token = await service.create_session(resources.db_sessions, body, user)
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return CreateSessionResponse(id=session_id, invite_token=token)

    @router.post("/join", response_model=JoinResponse)
    async def join_session(
        body: JoinRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> JoinResponse:
        try:
            session_id, seat, setup = await service.join_session(
                resources.db_sessions, resources.redis, body, user
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        dice_styles = await _dice_styles(resources.db_sessions, [seat.user_id])
        return JoinResponse(
            session_id=session_id, seat=service.seat_info(seat, setup, dice_styles=dice_styles)
        )

    @router.post("/{session_id}/observe", response_model=JoinResponse)
    async def observe_session(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> JoinResponse:
        """Вход наблюдателем (3.8): read-only место, только public-события журнала."""
        try:
            seat, setup = await service.observe_session(resources.db_sessions, session_id, user)
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        dice_styles = await _dice_styles(resources.db_sessions, [seat.user_id])
        return JoinResponse(
            session_id=session_id, seat=service.seat_info(seat, setup, dice_styles=dice_styles)
        )

    @router.delete("/{session_id}/observe", status_code=204)
    async def leave_observe(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> None:
        """Выход наблюдателя: своё spectator-место удаляется, сокет закрывается 4403."""
        try:
            await service.leave_observe(resources.db_sessions, resources.redis, session_id, user)
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error

    @router.delete("/{session_id}/seat", status_code=204)
    async def leave_seat(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> None:
        """Добровольный выход игрока/наблюдателя: своё место удаляется без бана.

        Повторная посадка возможна через зал/приглашение (в отличие от кика).
        Хост (dm) встать из-за стола не может — 422 cannot_leave_host.
        """
        try:
            await service.leave_seat(resources.db_sessions, resources.redis, session_id, user)
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error

    @router.get("/by-invite/{invite_token}", response_model=SessionPreviewResponse)
    async def session_preview(
        invite_token: Annotated[str, Path(min_length=1, max_length=64)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> SessionPreviewResponse:
        """Публичное превью стола по приглашению (задача #31): токен — capability.

        Без auth и без dm-секретов: только поля карточки стола. Регистрируется
        раньше ``/{session_id}``, чтобы путь не перехватывался UUID-роутом.
        """
        sessions = resources.db_sessions
        async with sessions() as db:
            row = await db.scalar(
                select(GameSessionRow).where(GameSessionRow.invite_token == invite_token)
            )
            if row is None:
                raise HTTPException(404, {"code": "invalid_invite"})
            title: str | None = None
            available_classes: list[str] = []
            if row.scenario_version_id is not None:
                version = (
                    await db.execute(
                        select(ScenarioRow.title, ScenarioVersionRow.info)
                        .join(ScenarioVersionRow, ScenarioVersionRow.scenario_id == ScenarioRow.id)
                        .where(ScenarioVersionRow.id == row.scenario_version_id)
                    )
                ).first()
                if version is not None:
                    title, info = version
                    available_classes = list(info.get("available_classes") or [])
            host_name: str | None = None
            if row.host_id is not None:
                host_name = await db.scalar(
                    select(UserRow.display_name).where(UserRow.id == row.host_id)
                )
            seats = (await db.scalars(select(SeatRow).where(SeatRow.session_id == row.id))).all()
        players = [seat for seat in seats if seat.role == "player"]
        return SessionPreviewResponse(
            id=row.id,
            scenario=row.scenario,
            scenario_title=title,
            scenario_version_id=row.scenario_version_id,
            sheet_template_id=row.sheet_template_id,
            language=row.language,
            dm_mode=row.dm_mode,
            status=row.status,
            seats_total=row.seats_total,
            seats_taken=len(players),
            taken_character_ids=[
                seat.character_id for seat in players if seat.character_id is not None
            ],
            host_name=host_name,
            available_classes=available_classes,
        )

    @router.get("/open-tables", response_model=list[OpenTableItem])
    async def open_tables(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> list[OpenTableItem]:
        """Зал историй (#32): столы с открытым набором и свободными местами.

        Регистрируется раньше ``/{session_id}``, чтобы путь не перехватывался
        UUID-роутом. ``invite_token`` отдаётся только recruiting-столов — посадка
        из зала идёт тем же механизмом, что и вход по приглашению (#31).
        """
        sessions = resources.db_sessions
        async with sessions() as db:
            rows = (
                await db.scalars(
                    select(GameSessionRow)
                    .where(
                        GameSessionRow.status == "active",
                        GameSessionRow.recruiting.is_(True),
                    )
                    .order_by(GameSessionRow.created_at.desc())
                )
            ).all()
            result: list[OpenTableItem] = []
            for row in rows:
                if row.invite_token is None:
                    continue
                seats = (
                    await db.scalars(select(SeatRow).where(SeatRow.session_id == row.id))
                ).all()
                players = [seat for seat in seats if seat.role == "player"]
                if row.seats_total is not None and len(players) >= row.seats_total:
                    continue
                title: str | None = None
                if row.scenario_version_id is not None:
                    title = await db.scalar(
                        select(ScenarioRow.title)
                        .join(ScenarioVersionRow, ScenarioVersionRow.scenario_id == ScenarioRow.id)
                        .where(ScenarioVersionRow.id == row.scenario_version_id)
                    )
                host_name: str | None = None
                if row.host_id is not None:
                    host_name = await db.scalar(
                        select(UserRow.display_name).where(UserRow.id == row.host_id)
                    )
                result.append(
                    OpenTableItem(
                        id=row.id,
                        scenario=row.scenario,
                        scenario_title=title,
                        language=row.language,
                        dm_mode=row.dm_mode,
                        seats_total=row.seats_total,
                        seats_taken=len(players),
                        host_name=host_name,
                        invite_token=row.invite_token,
                    )
                )
        return result

    @router.get("/{session_id}", response_model=SessionResponse)
    async def get_session(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> SessionResponse:
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        setup = SessionSetup.model_validate(row.setup)
        seats = await service.list_seats(sessions, session_id)
        events = await service.fetch_events(sessions, session_id, 0)
        dice_styles = await _dice_styles(sessions, [item.user_id for item in seats])
        return SessionResponse(
            id=row.id,
            scenario=row.scenario,
            language=row.language,
            dm_mode=row.dm_mode,
            status=row.status,
            last_seq=row.last_seq,
            state=service.state_for(row.state, seat),
            seat=service.seat_info(seat, setup, dice_styles=dice_styles),
            seats=[service.seat_info(item, setup, dice_styles=dice_styles) for item in seats],
            events=service.visible_events(events, seat),
            spectators=sum(1 for item in seats if item.role == "spectator"),
        )

    @router.get("/{session_id}/events", response_model=EventsResponse)
    async def get_events(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
        since: Annotated[int, Query(ge=0)] = 0,
    ) -> EventsResponse:
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        events = await service.fetch_events(sessions, session_id, since)
        return EventsResponse(events=service.visible_events(events, seat), last_seq=row.last_seq)

    @router.patch("/{session_id}/recruiting", response_model=RecruitingResponse)
    async def set_recruiting(
        session_id: uuid.UUID,
        body: RecruitingRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> RecruitingResponse:
        """Лидер команды открывает/закрывает набор за стол и задаёт ёмкость (#32)."""
        try:
            recruiting, seats_total = await service.set_recruiting(
                resources.db_sessions,
                session_id,
                recruiting=body.recruiting,
                seats_total=body.seats_total,
                user=user,
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return RecruitingResponse(recruiting=recruiting, seats_total=seats_total)

    @router.post("/{session_id}/moderation/mute", response_model=MuteResponse)
    async def moderation_mute(
        session_id: uuid.UUID,
        body: MuteRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> MuteResponse:
        """Мут/анмут игрока лидером команды: блокируются только текстовые действия."""
        try:
            await service.mute_seat(
                resources.db_sessions,
                resources.redis,
                session_id,
                body.seat_id,
                muted=body.muted,
                user=user,
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return MuteResponse(seat_id=body.seat_id, muted=body.muted)

    @router.post("/{session_id}/moderation/kick", response_model=KickResponse)
    async def moderation_kick(
        session_id: uuid.UUID,
        body: KickRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> KickResponse:
        """Кик игрока лидером команды: место удаляется, повторный вход запрещён."""
        try:
            await service.kick_seat(
                resources.db_sessions,
                resources.redis,
                session_id,
                body.seat_id,
                user=user,
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return KickResponse(seat_id=body.seat_id, removed=True)

    @router.post("/{session_id}/transfer-host", response_model=TransferHostResponse)
    async def transfer_host(
        session_id: uuid.UUID,
        body: TransferHostRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> TransferHostResponse:
        """Хост передаёт ведение игроку и встаёт из-за стола: своё место он теряет.

        Цель — player-место этой сессии; dm-место переходит новому хосту,
        player-место цели удаляется без бана. Комната узнаёт control-сообщением
        ``host_transferred`` (не seat_update: это не кик).
        """
        try:
            dm_seat_id, new_host_user_id, removed_seat_id = await service.transfer_host(
                resources.db_sessions, resources.redis, session_id, body.seat_id, user
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return TransferHostResponse(
            dm_seat_id=dm_seat_id,
            new_host_user_id=new_host_user_id,
            removed_seat_id=removed_seat_id,
        )

    @router.post("/{session_id}/stop", response_model=StopSessionResponse)
    async def stop_session(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> StopSessionResponse:
        """Лидер закрывает стол (остановка сессии): выход хоста, когда игроков нет.

        Идемпотентно: повторная остановка возвращает stopped=True. Возобновление
        стола — только админским надзором.
        """
        try:
            stopped = await service.stop_session_host(
                resources.db_sessions, resources.redis, session_id, user
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return StopSessionResponse(stopped=stopped)

    @router.post("/{session_id}/restart", response_model=RestartSessionResponse)
    async def restart_session(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> RestartSessionResponse:
        """Хост начинает сценарий сначала (после поражения или остановки): тот же стол.

        Журнал очищается, состояние — начальное (полные хиты, стартовая сцена),
        opening генерируется заново; setup/места/invite/чат сохраняются. Комната
        получает control-сообщение ``session_restarted`` и перечитывает снапшот.
        Идёт ход — 409 session_busy; не хост — 403 host_only.
        """
        try:
            state_version = await service.restart_session_host(
                resources.db_sessions, resources.redis, session_id, user
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, error.code) from error
        return RestartSessionResponse(state_version=state_version)

    @router.get("/{session_id}/chat", response_model=ChatHistoryResponse)
    async def get_chat(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        before: Annotated[uuid.UUID | None, Query()] = None,
    ) -> ChatHistoryResponse:
        """История чата вне игры: страница сообщений старше before (id сообщения)."""
        try:
            messages = await service.chat_history(
                resources.db_sessions, session_id, user, limit=limit, before=before
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, {"code": error.code}) from error
        return ChatHistoryResponse(messages=messages)

    @router.post("/{session_id}/chat", response_model=ChatMessageOut, status_code=201)
    async def post_chat(
        session_id: uuid.UUID,
        body: ChatRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> ChatMessageOut:
        """Сообщение чата вне игры: не команда Мастеру; без сознания не блокируется."""
        text = body.text.strip()
        if not text:
            raise HTTPException(422, {"code": "empty_message"})
        try:
            return await service.post_chat(
                resources.db_sessions, resources.redis, session_id, user, text
            )
        except service.SessionError as error:
            raise HTTPException(error.status_code, {"code": error.code}) from error

    @router.post("/{session_id}/complaints", response_model=ComplaintResponse, status_code=201)
    async def create_complaint(
        session_id: uuid.UUID,
        body: ComplaintRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> ComplaintResponse:
        """Жалоба в очередь модерации (3.9): только участник стола, без аудита."""
        sessions = resources.db_sessions
        async with sessions() as db:
            row = await db.get(GameSessionRow, session_id)
        if row is None:
            raise HTTPException(404, {"code": "session_not_found"})
        seat = await service.find_seat(sessions, session_id, user.user_id)
        if seat is None:
            raise HTTPException(403, {"code": "not_a_seat"})
        async with sessions() as db, db.begin():
            complaint = ComplaintRow(
                session_id=session_id,
                reporter_id=user.user_id,
                target_type=body.target_type,
                target_id=body.target_id,
                reason=body.reason,
            )
            db.add(complaint)
            await db.flush()
        return ComplaintResponse(id=complaint.id, status=complaint.status)

    return router


async def _dice_styles(
    sessions: service.Sessions, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Стили кубиков роллеров для SeatInfo: один запрос на всех участников ответа."""
    ids = [user_id for user_id in user_ids if user_id is not None]
    if not ids:
        return {}
    async with sessions() as db:
        rows = (
            await db.execute(select(UserRow.id, UserRow.ui_settings).where(UserRow.id.in_(ids)))
        ).all()
    styles: dict[uuid.UUID, str] = {}
    for user_id, ui_settings in rows:
        style = (ui_settings or {}).get("dice_style")
        if isinstance(style, str):
            styles[user_id] = style
    return styles


async def _session_and_seat(
    sessions: service.Sessions, session_id: uuid.UUID, user: CurrentUser
) -> tuple[GameSessionRow, SeatRow]:
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session_not_found")
    seat = await service.find_seat(sessions, session_id, user.user_id)
    if seat is None:
        raise HTTPException(403, "no_seat")
    return row, seat
