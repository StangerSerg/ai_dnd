"""Операции с сессиями и местами: создание, вход по приглашению, чтение журнала.

Создание переиспользует ``DbJournal.create`` (как консольный from-db путь); вход по
приглашению добавляет место в ``seats``, персонажа — в setup/state сессии, и сообщает
комнате событием ``ActorsJoined``. Ошибки — ``SessionError`` с HTTP-статусом и кодом.
"""

import secrets
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.admin import audit
from ai_dnd.characters.models import CharacterRow
from ai_dnd.characters.schemas import Character
from ai_dnd.characters.service import TemplateMismatchError, build_sheet
from ai_dnd.config import get_settings
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.game.content import GameContent, new_session_state
from ai_dnd.game.events import ActorsJoined
from ai_dnd.game.journal import DbJournal
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import PlayerSeat, SessionSetup, SessionState
from ai_dnd.game.skills import class_grants
from ai_dnd.identity.models import UserRow
from ai_dnd.realtime.auth import CurrentUser
from ai_dnd.realtime.broadcast import (
    BroadcastJournal,
    npc_locations_key,
    publish_chat_message,
    publish_host_transferred,
    publish_seat_update,
    publish_session_restarted,
    publish_session_stopped,
)
from ai_dnd.realtime.models import ChatMessageRow, SeatReadRow, SeatRow, SessionKickRow
from ai_dnd.realtime.schemas import (
    ChatMessageOut,
    CreateSessionRequest,
    EventOut,
    JoinRequest,
    SeatInfo,
)
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import Actor
from ai_dnd.rules.visibility import can_see
from ai_dnd.rulesets.models import RulesetRow
from ai_dnd.scenarios.models import ScenarioRow, ScenarioVersionRow
from ai_dnd.scenarios.store import load_game_content_from_db, version_refs
from ai_dnd.sheets.issues import SheetValuesError

Sessions = async_sessionmaker[AsyncSession]

DM_ACTOR_ID = "dm"

# Лимит наблюдателей на сессию (3.8): read-only места не занимают ёмкость стола.
SPECTATOR_LIMIT = 20


class SessionError(Exception):
    """Ошибка операции с сессией: HTTP-статус и машинный код."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """Результат создания стола: id, invite-токен, публичность, ёмкость и роль создателя."""

    session_id: uuid.UUID
    invite_token: str
    recruiting: bool
    seats_total: int | None
    role: str


async def find_seat(
    sessions: Sessions, session_id: uuid.UUID, user_id: uuid.UUID
) -> SeatRow | None:
    async with sessions() as db:
        return await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.user_id == user_id)
        )


async def list_seats(sessions: Sessions, session_id: uuid.UUID) -> list[SeatRow]:
    async with sessions() as db:
        return list(
            (
                await db.scalars(
                    select(SeatRow)
                    .where(SeatRow.session_id == session_id)
                    .order_by(SeatRow.created_at)
                )
            ).all()
        )


def seat_info(
    seat: SeatRow, setup: SessionSetup, dice_styles: Mapping[uuid.UUID, str] | None = None
) -> SeatInfo:
    player = setup.seat(seat.actor_id)
    dice_style = (
        (dice_styles or {}).get(seat.user_id, "amber") if seat.user_id is not None else "amber"
    )
    return SeatInfo(
        id=seat.id,
        role=seat.role,
        controller=seat.controller,
        user_id=seat.user_id,
        character_id=seat.character_id,
        actor_id=seat.actor_id,
        name=player.name if player is not None else None,
        muted=seat.muted,
        mute_source=seat.mute_source,
        dice_style=dice_style,
        skills=list(player.skills) if player is not None else [],
        cure_wounds=player.cure_wounds if player is not None else None,
    )


def event_out(row: GameEventRow) -> EventOut:
    return EventOut(seq=row.seq, type=row.type, visibility=row.visibility, payload=row.payload)


async def fetch_events(sessions: Sessions, session_id: uuid.UUID, since: int) -> list[GameEventRow]:
    async with sessions() as db:
        return list(
            (
                await db.scalars(
                    select(GameEventRow)
                    .where(GameEventRow.session_id == session_id, GameEventRow.seq > since)
                    .order_by(GameEventRow.seq)
                )
            ).all()
        )


def visible_events(rows: Iterable[GameEventRow], seat: SeatRow) -> list[EventOut]:
    """Фильтр видимости на сервере: dm и чужие player:<id> игроку не попадают."""
    return [
        event_out(row)
        for row in rows
        if can_see(row.visibility, seat.actor_id, dm=seat.role == "dm")
    ]


async def mark_seen(sessions: Sessions, seat_id: uuid.UUID, seq: int) -> None:
    async with sessions() as db, db.begin():
        await db.execute(
            update(SeatReadRow)
            .where(SeatReadRow.seat_id == seat_id)
            .values(last_seen_seq=func.greatest(SeatReadRow.last_seen_seq, seq))
        )


async def last_seen(sessions: Sessions, seat_id: uuid.UUID) -> int:
    async with sessions() as db:
        value = await db.scalar(
            select(SeatReadRow.last_seen_seq).where(SeatReadRow.seat_id == seat_id)
        )
    return value or 0


def state_for(state: dict[str, Any], seat: SeatRow) -> dict[str, Any]:
    """Снимок состояния для места: заметки и сводка ведущего игрокам не показываются."""
    if seat.role == "dm":
        return state
    return {**state, "facts": [], "summary": "", "summary_through_turn": 0}


def _require_character_owner(character: CharacterRow, user: CurrentUser) -> None:
    """За стол можно сесть только СВОИМ персонажем (как в characters.router: owner-only).

    Персонаж без владельца (legacy/консольные) через портал недоступен — тоже 403.
    """
    if character.owner_id is None or character.owner_id != user.user_id:
        raise SessionError(403, "character_forbidden")


def _player_seat_from_character(
    content: GameContent, character: CharacterRow, setup: SessionSetup
) -> tuple[Actor, PlayerSeat]:
    """Актёр и место игрока по персонажу: общая валидация для create_session и join_session.

    Ошибки: 409 template_mismatch (персонаж создан по другому шаблону), 422
    character_invalid (значения не проходят проверку шаблона).
    """
    try:
        character_data = Character.model_validate(character.data)
        sheet = build_sheet(character_data, content.template)
    except TemplateMismatchError as error:
        raise SessionError(409, "template_mismatch") from error
    except SheetValuesError as error:
        raise SessionError(422, "character_invalid") from error
    class_id = character_data.class_id
    actor_id = f"pc_{character.id.hex}"
    display = str(sheet.values.get("name") or character.name)
    granted, cure_wounds = class_grants(content.classes, content.skills, class_id, sheet)
    actor = actor_from_sheet(actor_id, display, sheet, skills=granted)
    player_seat = PlayerSeat(
        player_id=f"p{len(setup.seats) + 1}",
        actor_id=actor_id,
        name=display,
        summary=_summary(sheet.values),
        skills=granted,
        cure_wounds=cure_wounds,
    )
    return actor, player_seat


async def create_session(
    sessions: Sessions, body: CreateSessionRequest, user: CurrentUser
) -> CreatedSession:
    """Новая сессия по версии сценария; создатель — лидер стола (host_id).

    dm_mode="ai": настоящего Мастера ведёт ИИ, dm-место НЕ создаётся; создатель
    садится игроком со своим персонажем (character_id обязателен). dm_mode="human":
    прежнее поведение — создатель занимает dm-место, персонаж не нужен.
    Публичность (recruiting) и ёмкость (seats_total) задаются сразу при создании.
    """
    settings = get_settings()
    try:
        content = await load_game_content_from_db(
            sessions, body.scenario_version_id, settings.content_dir
        )
    except FileNotFoundError as error:
        raise SessionError(404, "scenario_version_not_found") from error
    if body.language != content.language:
        raise SessionError(422, "language_mismatch")
    async with sessions() as db:
        slug = await db.scalar(
            select(ScenarioRow.slug)
            .join(ScenarioVersionRow, ScenarioVersionRow.scenario_id == ScenarioRow.id)
            .where(ScenarioVersionRow.id == body.scenario_version_id)
        )
    if slug is None:
        raise SessionError(404, "scenario_version_not_found")
    character: CharacterRow | None = None
    if body.dm_mode == "ai":
        if body.character_id is None:
            raise SessionError(422, "character_required")
        async with sessions() as db:
            character = await db.get(CharacterRow, body.character_id)
        if character is None:
            raise SessionError(404, "character_not_found")
        _require_character_owner(character, user)
    ruleset_id, template_id = await version_refs(sessions, body.scenario_version_id)
    setup = SessionSetup(
        scenario=slug, language=content.language, improvisation=body.improvisation, seats=()
    )
    actors: tuple[Actor, ...] = ()
    if character is not None:
        # Создатель-игрок попадает в СТАРТОВОЕ состояние сессии: событие ActorsJoined
        # не пишется, opening сгенерирует воркер.
        actor, player_seat = _player_seat_from_character(content, character, setup)
        setup = SessionSetup(
            scenario=slug,
            language=content.language,
            improvisation=body.improvisation,
            seats=(player_seat,),
        )
        actors = (actor,)
    state = new_session_state(content, actors)
    journal = await DbJournal.create(
        sessions,
        setup,
        state,
        scenario_version_id=body.scenario_version_id,
        ruleset_id=ruleset_id,
        sheet_template_id=template_id,
    )
    session_id = journal.session_id
    token = secrets.token_urlsafe(32)
    async with sessions() as db, db.begin():
        row = await db.get(GameSessionRow, session_id)
        if row is None:
            raise SessionError(404, "session_not_found")
        row.host_id = user.user_id
        row.invite_token = token
        row.dm_mode = body.dm_mode
        row.recruiting = body.recruiting
        row.seats_total = body.seats_total
        if character is not None:
            seat = SeatRow(
                session_id=session_id,
                role="player",
                controller="human",
                user_id=user.user_id,
                character_id=character.id,
                actor_id=f"pc_{character.id.hex}",
            )
            role = "player"
        else:
            seat = SeatRow(
                session_id=session_id,
                role="dm",
                controller="human",
                user_id=user.user_id,
                actor_id=DM_ACTOR_ID,
            )
            role = "dm"
        db.add(seat)
        await db.flush()
        db.add(SeatReadRow(seat_id=seat.id))
    if body.dm_mode == "ai":
        # Открытие ведёт воркер (первый рассказ без действий игроков); задача
        # идемпотентна: если рассказ уже есть в журнале, повторный запуск — no-op.
        from ai_dnd.tasks import dm_open_session

        await dm_open_session.kiq(str(session_id))
    return CreatedSession(
        session_id=session_id,
        invite_token=token,
        recruiting=body.recruiting,
        seats_total=body.seats_total,
        role=role,
    )


async def join_session(
    sessions: Sessions, redis: Redis, body: JoinRequest, user: CurrentUser
) -> tuple[uuid.UUID, SeatRow, SessionSetup]:
    """Занять место игрока по приглашению, выбрав готового персонажа.

    Повторный вход того же пользователя возвращает его место (идемпотентно).
    """
    settings = get_settings()
    async with sessions() as db:
        # Архивный стол считается несуществующим: вход по приглашению закрыт.
        session_id = await db.scalar(
            select(GameSessionRow.id).where(
                GameSessionRow.invite_token == body.invite_token,
                GameSessionRow.archived.is_(False),
            )
        )
    if session_id is None:
        raise SessionError(404, "invalid_invite")
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
        )
        if row is None:
            raise SessionError(404, "invalid_invite")
        if row.status != "active":
            raise SessionError(409, "session_stopped")
        kicked = await db.scalar(
            select(SessionKickRow.user_id).where(
                SessionKickRow.session_id == session_id,
                SessionKickRow.user_id == user.user_id,
            )
        )
        if kicked is not None:
            raise SessionError(403, "kicked")
        setup = SessionSetup.model_validate(row.setup)
        existing = await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.user_id == user.user_id)
        )
        if existing is not None:
            return session_id, existing, setup
        if row.seats_total is not None:
            taken = len(
                (
                    await db.scalars(
                        select(SeatRow.id).where(
                            SeatRow.session_id == session_id, SeatRow.role == "player"
                        )
                    )
                ).all()
            )
            if taken >= row.seats_total:
                raise SessionError(409, "table_full")
        if row.scenario_version_id is None:
            raise SessionError(409, "session_without_scenario")
        character = await db.get(CharacterRow, body.character_id)
        if character is None:
            raise SessionError(404, "character_not_found")
        _require_character_owner(character, user)
        content = await load_game_content_from_db(
            sessions, row.scenario_version_id, settings.content_dir
        )
        actor, player_seat = _player_seat_from_character(content, character, setup)
        try:
            character_data = Character.model_validate(character.data)
            sheet = build_sheet(character_data, content.template)
        except TemplateMismatchError as error:
            raise SessionError(409, "template_mismatch") from error
        except SheetValuesError as error:
            raise SessionError(422, "character_invalid") from error
        # Сценарный блок «доступные классы»: пусто — все классы (обратная совместимость).
        available = content.scenario.scenario.available_classes
        if available and character_data.class_id not in available:
            raise SessionError(422, "class_not_available")
        actor_id = f"pc_{character.id.hex}"
        if setup.seat(player_seat.actor_id) is not None:
            raise SessionError(409, "character_taken")
        actor_id = player_seat.actor_id
        new_setup = setup.model_copy(update={"seats": (*setup.seats, player_seat)})
        state = SessionState.model_validate(row.state)
        new_state = state.model_copy(update={"rules": state.rules.with_actor(actor)})
        row.setup = new_setup.model_dump(mode="json")
        row.state = new_state.model_dump(mode="json")
        row.state_version += 1
        seat = SeatRow(
            session_id=session_id,
            role="player",
            controller="human",
            user_id=user.user_id,
            character_id=character.id,
            actor_id=actor_id,
        )
        db.add(seat)
        await db.flush()
        db.add(SeatReadRow(seat_id=seat.id))
    # Комната узнаёт о новом участнике: событие фиксируется в журнале и рассылается.
    journal = BroadcastJournal(DbJournal(sessions, session_id), redis, sessions)
    await journal.append([ActorsJoined(actors=(actor,))], new_state)
    return session_id, seat, new_setup


def _summary(values: dict[str, Any]) -> str:
    race = values.get("race")
    char_class = values.get("char_class")
    level = values.get("level")
    parts = [str(race)] if race else []
    if char_class:
        parts.append(f"{char_class} {level}" if level else str(char_class))
    return ", ".join(parts)


# --- Наблюдатели за столом (задача #21, подэтап 3.8) ---


async def observe_session(
    sessions: Sessions, session_id: uuid.UUID, user: CurrentUser
) -> tuple[SeatRow, SessionSetup]:
    """Read-only место наблюдателя: только public-события, без персонажа и действий.

    Один пользователь — одно место: player/dm наблюдателем не становится
    (409 already_seated), повторный вход spectator идемпотентен. Кикнутые
    лидером не допускаются и к наблюдению.
    """
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
        )
        # Архивный стол считается несуществующим: наблюдение закрыто.
        if row is None or row.archived:
            raise SessionError(404, "session_not_found")
        if row.status != "active":
            raise SessionError(409, "session_stopped")
        kicked = await db.scalar(
            select(SessionKickRow.user_id).where(
                SessionKickRow.session_id == session_id,
                SessionKickRow.user_id == user.user_id,
            )
        )
        if kicked is not None:
            raise SessionError(403, "kicked")
        setup = SessionSetup.model_validate(row.setup)
        existing = await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.user_id == user.user_id)
        )
        if existing is not None:
            if existing.role != "spectator":
                raise SessionError(409, "already_seated")
            return existing, setup
        taken = await db.scalar(
            select(func.count())
            .select_from(SeatRow)
            .where(SeatRow.session_id == session_id, SeatRow.role == "spectator")
        )
        if int(taken or 0) >= SPECTATOR_LIMIT:
            raise SessionError(409, "spectators_full")
        # Уникальный в сессии actor_id: can_see с dm=False пропускает только public.
        actor_id = f"spec_{secrets.token_hex(4)}"
        while (
            await db.scalar(
                select(SeatRow.id).where(
                    SeatRow.session_id == session_id, SeatRow.actor_id == actor_id
                )
            )
            is not None
        ):
            actor_id = f"spec_{secrets.token_hex(4)}"
        seat = SeatRow(
            session_id=session_id,
            role="spectator",
            controller="human",
            user_id=user.user_id,
            actor_id=actor_id,
        )
        db.add(seat)
        await db.flush()
        db.add(SeatReadRow(seat_id=seat.id))
    return seat, setup


async def leave_observe(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, user: CurrentUser
) -> None:
    """Добровольный выход наблюдателя: место удаляется, сокет закрывается 4403."""
    async with sessions() as db, db.begin():
        seat = await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.user_id == user.user_id)
        )
        if seat is None:
            raise SessionError(404, "seat_not_found")
        if seat.role != "spectator":
            raise SessionError(409, "not_spectator")
        seat_id = seat.id
        await db.delete(seat)
    await publish_seat_update(redis, session_id, seat_id, muted=False, removed=True)


async def leave_seat(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, user: CurrentUser
) -> None:
    """Добровольный выход игрока/наблюдателя: своё место удаляется БЕЗ бана.

    В отличие от kick, SessionKickRow не создаётся — сесть снова можно через
    зал/приглашение. Лидер стола не «встаёт» (422): лидерство определяется
    host_id сессии (в ai-режиме лидер — игрок), а не ролью места; остановка
    сессии — отдельная механика. После удаления пересчитывается окно действий
    (как в kick_seat): если оставшиеся игроки уже ответили, окно закрывается
    и ход ставится.
    """
    async with sessions() as db, db.begin():
        row = await db.get(GameSessionRow, session_id)
        if row is None:
            raise SessionError(404, "session_not_found")
        seat = await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.user_id == user.user_id)
        )
        if seat is None:
            raise SessionError(404, "seat_not_found")
        if seat.role == "dm" or (row.host_id is not None and seat.user_id == row.host_id):
            raise SessionError(422, "cannot_leave_host")
        if seat.role not in ("player", "spectator"):
            raise SessionError(404, "seat_not_found")
        seat_id = seat.id
        await audit.record(
            db,
            user.user_id,
            "seat.leave",
            "seat",
            str(seat_id),
            {"user_id": str(user.user_id), "actor_id": seat.actor_id, "role": seat.role},
        )
        await db.delete(seat)
    await publish_seat_update(redis, session_id, seat_id, muted=False, removed=True)
    # Локальный импорт: turns ссылается на service, обратная зависимость — только тут.
    from ai_dnd.realtime.turns import try_close_and_dispatch, window_key

    window_raw = await redis.get(window_key(session_id))
    if window_raw is not None:
        window_id = window_raw.decode("utf-8") if isinstance(window_raw, bytes) else str(window_raw)
        await try_close_and_dispatch(sessions, redis, session_id, window_id)


# --- Модерация стола лидером команды (задача #25) ---


async def _require_host(sessions: Sessions, session_id: uuid.UUID, user: CurrentUser) -> None:
    """Модерировать стол может только создатель сессии."""
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None:
        raise SessionError(404, "session_not_found")
    if row.host_id != user.user_id:
        raise SessionError(403, "host_only")


async def set_recruiting(
    sessions: Sessions,
    session_id: uuid.UUID,
    *,
    recruiting: bool,
    seats_total: int | None,
    user: CurrentUser,
) -> tuple[bool, int | None]:
    """Лидер команды открывает/закрывает набор за стол и задаёт ёмкость (#32)."""
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        row = await db.get(GameSessionRow, session_id)
        if row is None:
            raise SessionError(404, "session_not_found")
        row.recruiting = recruiting
        row.seats_total = seats_total
    return recruiting, seats_total


async def mute_seat(
    sessions: Sessions,
    redis: Redis,
    session_id: uuid.UUID,
    seat_id: uuid.UUID,
    *,
    muted: bool,
    user: CurrentUser,
) -> None:
    """Мут player/spectator-места: блокируются команды/действия, чат вне игры открыт.

    Нокаут в бою переиспользует этот же мут (source=knockout, см.
    sync_knockout_mutes); лидерский мут (source=leader) автосинхронизация не трогает.
    В журнал событий модерация не пишется (долг прототипа): комната узнаёт
    эфемерным control-сообщением ``seat_update`` без seq.
    """
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        seat = await db.get(SeatRow, seat_id)
        if (
            seat is None
            or seat.session_id != session_id
            or seat.role not in ("player", "spectator")
        ):
            raise SessionError(404, "seat_not_found")
        # Мут нокаута лидер снять не может: пока актёр без сознания, мут
        # снимается только лечением/подъёмом (sync_knockout_mutes). Лидерский
        # мут упавшего актёра тоже защищён — иначе тот успеет подействовать.
        if not muted and seat.role == "player":
            row = await db.get(GameSessionRow, session_id)
            ruleset_row = (
                await db.get(RulesetRow, row.ruleset_id)
                if row is not None and row.ruleset_id is not None
                else None
            )
            if row is not None and ruleset_row is not None:
                state = SessionState.model_validate(row.state)
                actor = state.rules.actors.get(seat.actor_id)
                if _actor_is_downed(actor, Ruleset.model_validate(ruleset_row.data)):
                    raise SessionError(422, "cannot_unmute_knockout")
        seat.muted = muted
        seat.mute_source = "leader" if muted else None
        await audit.record(db, user.user_id, "seat.mute", "seat", str(seat_id), {"muted": muted})
    await publish_seat_update(
        redis, session_id, seat_id, muted=muted, removed=False, mute_source=seat.mute_source
    )


async def kick_seat(
    sessions: Sessions,
    redis: Redis,
    session_id: uuid.UUID,
    seat_id: uuid.UUID,
    *,
    user: CurrentUser,
) -> None:
    """Кик player/spectator-места: место удаляется (seat_reads каскадом), вход запрещается.

    После удаления пересчитывается окно действий: если оставшиеся игроки уже
    ответили, окно закрывается и ход ставится (stale/locked/empty игнорируются —
    ход мог уже идти). Наблюдатель в окне не участвует, пересчёт для него безвреден.
    """
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        row = await db.get(GameSessionRow, session_id)
        if row is None:
            raise SessionError(404, "session_not_found")
        seat = await db.get(SeatRow, seat_id)
        if seat is None or seat.session_id != session_id:
            raise SessionError(404, "seat_not_found")
        if seat.role == "dm":
            raise SessionError(422, "cannot_kick_host")
        # Лидера (host_id сессии) кикнуть нельзя: в ai-режиме лидер — игрок.
        if row.host_id is not None and seat.user_id == row.host_id:
            raise SessionError(422, "cannot_kick_host")
        if seat.role not in ("player", "spectator"):
            raise SessionError(404, "seat_not_found")
        if seat.user_id is not None:
            await db.execute(
                pg_insert(SessionKickRow)
                .values(session_id=session_id, user_id=seat.user_id)
                .on_conflict_do_nothing()
            )
        await audit.record(
            db,
            user.user_id,
            "seat.kick",
            "seat",
            str(seat_id),
            {"user_id": str(seat.user_id), "actor_id": seat.actor_id},
        )
        await db.delete(seat)
    await publish_seat_update(redis, session_id, seat_id, muted=False, removed=True)
    # Локальный импорт: turns ссылается на service, обратная зависимость — только тут.
    from ai_dnd.realtime.turns import try_close_and_dispatch, window_key

    window_raw = await redis.get(window_key(session_id))
    if window_raw is not None:
        window_id = window_raw.decode("utf-8") if isinstance(window_raw, bytes) else str(window_raw)
        await try_close_and_dispatch(sessions, redis, session_id, window_id)


async def transfer_host(
    sessions: Sessions,
    redis: Redis,
    session_id: uuid.UUID,
    seat_id: uuid.UUID,
    user: CurrentUser,
) -> tuple[uuid.UUID | None, uuid.UUID, uuid.UUID | None]:
    """Передача лидерства игроку; возвращает (dm_seat_id, new_host_user_id, removed_seat_id).

    Цель — player-место этой сессии с user_id: чужое/несуществующее место — 404
    seat_not_found; dm/spectator/место без пользователя — 422 invalid_transfer_target;
    остановленный стол — 409 session_stopped. Прежний лидер остаётся игроком.

    Если в сессии ЕСТЬ dm-место (human-режим и старые ai-столы): прежнее поведение —
    dm-место получает user_id цели (actor_id остаётся «dm»), player-место цели
    удаляется без бана, в ответе оба id заполнены. Если dm-места НЕТ (ai-режим):
    host_id просто переходит к цели, цель СОХРАНЯЕТ своё player-место и персонажа,
    dm_seat_id/removed_seat_id — None (никто не теряет место, сокеты не закрываются).

    После коммита — эфемерный control ``host_transferred`` (не seat_update
    removed=True: удаление своего места фронтенд считает киком) и пересчёт окна
    действий, как в leave_seat/kick_seat.
    """
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
        )
        if row is None:
            raise SessionError(404, "session_not_found")
        if row.status != "active":
            raise SessionError(409, "session_stopped")
        dm_seat = await db.scalar(
            select(SeatRow).where(SeatRow.session_id == session_id, SeatRow.role == "dm")
        )
        target = await db.get(SeatRow, seat_id)
        if target is None or target.session_id != session_id:
            raise SessionError(404, "seat_not_found")
        if target.role != "player" or target.user_id is None:
            raise SessionError(422, "invalid_transfer_target")
        new_host_user_id = target.user_id
        row.host_id = new_host_user_id
        dm_seat_id: uuid.UUID | None = None
        removed_seat_id: uuid.UUID | None = None
        removed_actor_id: str | None = None
        if dm_seat is not None:
            # Human-режим (в т.ч. старые столы): dm-место переходит цели, её
            # player-место удаляется.
            dm_seat_id = dm_seat.id
            removed_seat_id = target.id
            removed_actor_id = target.actor_id
            dm_seat.user_id = new_host_user_id
            await db.delete(target)
        await audit.record(
            db,
            user.user_id,
            "seat.transfer_host",
            "seat",
            str(seat_id),
            {
                "old_host_user_id": str(user.user_id),
                "new_host_user_id": str(new_host_user_id),
                "dm_seat_id": str(dm_seat_id) if dm_seat_id is not None else None,
                "removed_actor_id": removed_actor_id,
            },
        )
    await publish_host_transferred(
        redis,
        session_id,
        dm_seat_id=dm_seat_id,
        new_host_user_id=new_host_user_id,
        removed_seat_id=removed_seat_id,
    )
    # Локальный импорт: turns ссылается на service, обратная зависимость — только тут.
    from ai_dnd.realtime.turns import try_close_and_dispatch, window_key

    window_raw = await redis.get(window_key(session_id))
    if window_raw is not None:
        window_id = window_raw.decode("utf-8") if isinstance(window_raw, bytes) else str(window_raw)
        await try_close_and_dispatch(sessions, redis, session_id, window_id)
    return dm_seat_id, new_host_user_id, removed_seat_id


async def stop_session_host(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, user: CurrentUser
) -> bool:
    """Лидер закрывает стол: остановка сессии, когда ведение некому передать.

    Идемпотентно: повторный вызов на остановленном столе возвращает stopped=True
    без ошибки и без повторной публикации. Возобновление — только админским
    надзором (admin/supervision). LLM не вызывается.
    """
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
        )
        if row is None:
            raise SessionError(404, "session_not_found")
        already_stopped = row.status == "stopped"
        if not already_stopped:
            row.status = "stopped"
            await audit.record(
                db, user.user_id, "session.stop", "session", str(session_id), {"stopped": True}
            )
    if not already_stopped:
        await publish_session_stopped(redis, session_id, stopped=True)
    return True


async def archive_session(sessions: Sessions, session_id: uuid.UUID, user: CurrentUser) -> bool:
    """Безопасное удаление стола лидером: стол скрывается из списков, запись остаётся в БД.

    Только для остановленного стола (status == "stopped"), иначе 409
    session_not_stopped. Идемпотентно: повторный вызов на архивном столе
    возвращает archived=True без ошибки. Эффект: archived=True, recruiting=False
    (стол уходит из зала); в Redis ничего не публикуется — сокетов архив не рвёт.
    """
    await _require_host(sessions, session_id, user)
    async with sessions() as db, db.begin():
        row = await db.scalar(
            select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
        )
        if row is None:
            raise SessionError(404, "session_not_found")
        if row.archived:
            return True
        if row.status != "stopped":
            raise SessionError(409, "session_not_stopped")
        row.archived = True
        row.recruiting = False
        await audit.record(
            db, user.user_id, "session.archive", "session", str(session_id), {"archived": True}
        )
    return True


async def restart_session_host(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, user: CurrentUser
) -> int:
    """Хост начинает сценарий сначала: журнал очищается, состояние и opening — с нуля.

    Стол тот же: setup/seats/invite/host/recruiting/chat/kicks сохраняются; рестарт
    разрешён и после остановки (status снова active). Только под замком комнаты:
    активный ход не должен дописать события после сброса. Замок не ждём — LLM-ход
    может идти минуты, HTTP-запрос столько не висит; занятый стол — 409 session_busy,
    хост повторяет запрос. Возвращает новый state_version.
    """
    await _require_host(sessions, session_id, user)
    # Локальный импорт: turns ссылается на service, обратная зависимость — только тут.
    from ai_dnd.realtime.turns import _acquire_lock, _release_lock, pending_key, window_key

    token = uuid.uuid4().hex
    if not await _acquire_lock(redis, session_id, token):
        raise SessionError(409, "session_busy")
    settings = get_settings()
    try:
        async with sessions() as db, db.begin():
            row = await db.scalar(
                select(GameSessionRow).where(GameSessionRow.id == session_id).with_for_update()
            )
            if row is None:
                raise SessionError(404, "session_not_found")
            if row.scenario_version_id is None:
                raise SessionError(409, "session_without_scenario")
            content = await load_game_content_from_db(
                sessions, row.scenario_version_id, settings.content_dir
            )
            seats = (
                await db.scalars(
                    select(SeatRow).where(
                        SeatRow.session_id == session_id, SeatRow.role == "player"
                    )
                )
            ).all()
            actors: list[Actor] = []
            for seat in seats:
                if seat.character_id is None:
                    raise SessionError(409, "seat_without_character")
                character = await db.get(CharacterRow, seat.character_id)
                if character is None:
                    raise SessionError(404, "character_not_found")
                try:
                    character_data = Character.model_validate(character.data)
                    sheet = build_sheet(character_data, content.template)
                except TemplateMismatchError as error:
                    raise SessionError(409, "template_mismatch") from error
                except SheetValuesError as error:
                    raise SessionError(422, "character_invalid") from error
                granted, _ = class_grants(
                    content.classes, content.skills, character_data.class_id, sheet
                )
                actors.append(
                    actor_from_sheet(
                        f"pc_{character.id.hex}",
                        str(sheet.values.get("name") or character.name),
                        sheet,
                        skills=granted,
                    )
                )
            state = new_session_state(content, actors)
            await db.execute(delete(GameEventRow).where(GameEventRow.session_id == session_id))
            # WS-replay стартует с last_seen места: без сброса клиент не увидит новый журнал.
            await db.execute(
                update(SeatReadRow)
                .where(
                    SeatReadRow.seat_id.in_(
                        select(SeatRow.id).where(SeatRow.session_id == session_id)
                    )
                )
                .values(last_seen_seq=0)
            )
            row.last_seq = 0
            row.summary = None
            row.state_version += 1
            row.status = "active"
            row.state = state.model_dump(mode="json")
            state_version = row.state_version
            dm_mode = row.dm_mode
            await audit.record(
                db,
                user.user_id,
                "session.restart",
                "session",
                str(session_id),
                {"state_version": state_version},
            )
        # Старое окно сбора действий не должно пережить сброс игры; кэш локаций NPC
        # тоже сбрасывается (состояние снова стартует с home сценария).
        await redis.delete(
            pending_key(session_id), window_key(session_id), npc_locations_key(session_id)
        )
        await publish_session_restarted(redis, session_id, state_version=state_version)
        # Нокаут-муты снимутся синхронизацией с полным HP (лидерский мут не трогается).
        await sync_knockout_mutes(sessions, redis, session_id)
        if dm_mode == "ai":
            # Журнал очищен → идемпотентная задача opening сгенерирует рассказ заново.
            from ai_dnd.tasks import dm_open_session

            await dm_open_session.kiq(str(session_id))
        return state_version
    finally:
        await _release_lock(redis, session_id, token)


def _chat_out(row: ChatMessageRow) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id, user_id=row.user_id, author=row.author_name, text=row.text, at=row.created_at
    )


async def chat_history(
    sessions: Sessions,
    session_id: uuid.UUID,
    user: CurrentUser,
    *,
    limit: int = 50,
    before: uuid.UUID | None = None,
) -> list[ChatMessageOut]:
    """История чата вне игры: только участник стола; страница старше сообщения before."""
    seat = await find_seat(sessions, session_id, user.user_id)
    if seat is None:
        raise SessionError(403, "no_seat")
    async with sessions() as db:
        query = select(ChatMessageRow).where(ChatMessageRow.session_id == session_id)
        if before is not None:
            anchor = await db.get(ChatMessageRow, before)
            if anchor is not None and anchor.session_id == session_id:
                query = query.where(
                    or_(
                        ChatMessageRow.created_at < anchor.created_at,
                        (ChatMessageRow.created_at == anchor.created_at)
                        & (ChatMessageRow.id < anchor.id),
                    )
                )
        rows = (
            await db.scalars(
                query.order_by(ChatMessageRow.created_at.desc(), ChatMessageRow.id.desc()).limit(
                    limit
                )
            )
        ).all()
    return [_chat_out(row) for row in reversed(rows)]


async def post_chat(
    sessions: Sessions,
    redis: Redis,
    session_id: uuid.UUID,
    user: CurrentUser,
    text: str,
) -> ChatMessageOut:
    """Чат вне игры: открыт любому месту, в том числе замьюченному и без сознания.

    Мут и нокаут блокируют только команды/действия; разговор за столом остаётся
    доступен (решение пользователя 19.11). Кикнутый места не имеет — 403.
    """
    seat = await find_seat(sessions, session_id, user.user_id)
    if seat is None:
        raise SessionError(403, "no_seat")
    async with sessions() as db:
        author = await db.scalar(select(UserRow.display_name).where(UserRow.id == user.user_id))
    async with sessions() as db, db.begin():
        row = ChatMessageRow(
            session_id=session_id,
            user_id=user.user_id,
            author_name=author if author is not None else "Игрок",
            text=text,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        out = _chat_out(row)
    await publish_chat_message(redis, session_id, out.model_dump(mode="json"))
    return out


def _actor_is_downed(actor: Actor | None, ruleset: Ruleset) -> bool:
    """Актёр не может действовать: смерть, hp<=0 или prevents_actions-состояние."""
    if actor is None:
        return False
    return bool(
        actor.dead
        or (actor.hp is not None and actor.hp.current <= 0)
        or any(
            (definition := ruleset.conditions.get(active.id)) is not None
            and definition.prevents_actions
            for active in actor.conditions
        )
    )


async def sync_knockout_mutes(sessions: Sessions, redis: Redis, session_id: uuid.UUID) -> None:
    """Нокаут переиспользует мут: синхронизация muted с состоянием актёров после хода.

    hp<=0/смерть/prevents_actions-состояние -> muted с source=knockout (лидерский
    мут не перетирается); при подъёме снимается только knockout-мут. Идемпотентно:
    seat_update публикуется лишь при фактическом изменении места.
    """
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None or row.ruleset_id is None:
        return
    async with sessions() as db:
        ruleset_row = await db.get(RulesetRow, row.ruleset_id)
    if ruleset_row is None:
        return
    ruleset = Ruleset.model_validate(ruleset_row.data)
    state = SessionState.model_validate(row.state)
    async with sessions() as db:
        seats = (
            await db.scalars(
                select(SeatRow).where(
                    SeatRow.session_id == session_id,
                    SeatRow.role == "player",
                    SeatRow.controller == "human",
                )
            )
        ).all()
    changes: list[tuple[uuid.UUID, bool, str | None]] = []
    for seat in seats:
        actor = state.rules.actors.get(seat.actor_id)
        if actor is None:
            continue
        downed = _actor_is_downed(actor, ruleset)
        if downed:
            if seat.muted and seat.mute_source in ("leader", "knockout"):
                continue
            changes.append((seat.id, True, "knockout"))
        elif seat.mute_source == "knockout":
            changes.append((seat.id, False, None))
    if not changes:
        return
    async with sessions() as db, db.begin():
        for seat_id, muted, source in changes:
            await db.execute(
                update(SeatRow).where(SeatRow.id == seat_id).values(muted=muted, mute_source=source)
            )
    for seat_id, muted, source in changes:
        await publish_seat_update(
            redis, session_id, seat_id, muted=muted, removed=False, mute_source=source
        )
