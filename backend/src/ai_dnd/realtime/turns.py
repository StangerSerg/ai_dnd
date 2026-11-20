"""Ход AI DM в веб-сессии: окно действий, блокировка комнаты, стриминг, биллинг (3.3).

Точка входа игроков — ``POST /api/sessions/{id}/actions`` (роль player; dm и будущие
spectator получают 403 — задел подэтапа 3.8). Действия копятся в Redis-хэше
``session:{id}:pending`` (actor_id -> text); окно идентифицирует ключ
``session:{id}:window``. Окно закрывается, когда действия дали все player-места,
или по таймауту (задача ``close_action_window`` с window_id: устаревшее закрытие
не трогает новое окно). Закрытие атомарно забирает pending (Lua) и ставит задачу
воркера ``dm_turn``; повторное закрытие того же окна — no-op.

Блокировка комнаты — ``session:{id}:turn_lock`` (SET NX PX, value — токен владельца,
снятие со сверкой токена). Пока замок держится, явный триггер ``POST /{id}/turn``
отвечает 409 turn_in_progress, а действия принимаются в новое окно и не теряются:
после хода воркер закрывает накопившееся окно сам. Воркер без замка — no-op
(защита от дублей задач); additionally маркер ``session:{id}:turn_done:{window_id}``
делает повторный dm_turn того же окна no-op.

Протокол сообщений канала комнаты (см. broadcast.py и ws.py): журнальные события
имеют seq/type/visibility/payload; эфемерные chunk-сообщения стриминга —
``{"type": "narration_chunk", "payload": {"text": ...}}`` без seq и visibility.
Chunk — публичный голос DM: фильтр видимости не нужен, в журнал и seat_reads не
пишется, при переподключении не досылается; полный текст приходит финальным
журнальным событием Narrated.

Стриминг: GameMaster вызывает complete("narration") и отдаёт NarrationChunk уже
после коммита Narrated, поэтому адаптер ``_RoomGateway`` сам потребляет
BillingGuard.stream и публикует TextDelta в канал до финального события;
NarrationChunk из master воркер не публикует повторно. Адаптер также подставляет
плательщика (host сессии) в CallContext каждого вызова — GameMaster и build_summary
контекст с user_id не передают.

dm_mode="human": AI-оркестрация не работает, действие сразу пишется в журнал как
PlayerActed через BroadcastJournal (без окна, воркера и биллинга).

Web-бой (3.4): ``GET /{id}/combat-options`` считает варианты чистым движком из
state.rules (без GameMaster/LLM); ``POST /{id}/combat-actions`` валидирует индекс
против свежих вариантов и ставит задачу ``combat_step``. Воркер
``run_combat_step`` выполняет действие под замком комнаты и крутит боевой цикл
как console._combat: ходы существ, рассказ раунда стримом, авто-действие при
единственном варианте; останавливается на party-актёре с human-местом (ждёт HTTP)
или после CombatEnded с финальным рассказом. Тот же цикл (``_advance_combat``)
запускается сразу после текстового хода, начавшего бой, и при повторном тексте
в бою — очередь не остаётся на существе (G1).
"""

import uuid
from collections.abc import Sequence
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway, build_gateway
from ai_dnd.ai.provider import LLMError
from ai_dnd.ai.recorder import CallContext, CompositeRecorder, DbRecorder, MemoryRecorder
from ai_dnd.ai.schemas import LLMResponse, Message, StreamEnd, TextDelta, ToolChoice, ToolSpec
from ai_dnd.billing.errors import BillingStop
from ai_dnd.billing.guard import BillingGuard
from ai_dnd.billing.ledger import CreditLedger
from ai_dnd.billing.limits import BillingLimits
from ai_dnd.config import Settings
from ai_dnd.game.events import Narrated, PlayerActed
from ai_dnd.game.journal import DbJournal
from ai_dnd.game.master import GameMaster, PlayerInput
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.rag.search import LoreSearcher
from ai_dnd.realtime import service
from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.realtime.broadcast import BroadcastJournal, publish_chunk
from ai_dnd.realtime.models import SeatRow
from ai_dnd.resources import Resources, get_resources
from ai_dnd.rules.actions import (
    Action,
    AttackAction,
    EndTurnAction,
    FleeAction,
    HideAction,
    ProtectAction,
)
from ai_dnd.rules.combat import incapacitated_by
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.rng import SystemRng
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import GameState
from ai_dnd.rules.tactics import choose_action
from ai_dnd.rules.visibility import for_player
from ai_dnd.rulesets.models import RulesetRow
from ai_dnd.scenarios.store import load_game_content_from_db

Sessions = async_sessionmaker[AsyncSession]


class TurnSettings(BaseSettings):
    """Настройки оркестрации хода (env AI_DND_*, отдельный класс — config.py не меняется)."""

    model_config = SettingsConfigDict(
        env_prefix="AI_DND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Окно сбора действий: закрывается по полноте или по этому таймауту.
    turn_window_seconds: int = Field(default=180, ge=1)
    # Замок комнаты на время хода (мс): страховка от зависшего воркера.
    turn_lock_ms: int = Field(default=180_000, ge=1000)
    # Повтор закрытия окна, если в момент таймаута комната занята ходом.
    turn_close_retry_seconds: int = Field(default=5, ge=1)
    # Модель-заглушка для задач воркера, когда fake не передан явно (смоки, dev).
    fake_llm: bool = False


def get_turn_settings() -> TurnSettings:
    """Без lru_cache: смоки и dev меняют окно через env между запусками."""
    return TurnSettings()


def resolve_fake(fake: bool | None) -> bool:
    return get_turn_settings().fake_llm if fake is None else fake


# --- Redis-ключи комнаты ---


def pending_key(session_id: uuid.UUID) -> str:
    return f"session:{session_id}:pending"


def window_key(session_id: uuid.UUID) -> str:
    return f"session:{session_id}:window"


def lock_key(session_id: uuid.UUID) -> str:
    return f"session:{session_id}:turn_lock"


def _done_key(session_id: uuid.UUID, window_id: str) -> str:
    return f"session:{session_id}:turn_done:{window_id}"


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


# Атомарный приём действия: записать в pending, создать окно при первом действии.
# Возвращает [created(0/1), window_id, pending_count].
_SUBMIT_LUA = """
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
local created = redis.call('SET', KEYS[2], ARGV[3], 'NX')
local window = redis.call('GET', KEYS[2])
return {created and 1 or 0, window, redis.call('HLEN', KEYS[1])}
"""

# Атомарное закрытие окна: только своё окно, только без замка, pending забирается целиком.
# Возвращает [status, flat items]: 0 stale, 1 locked, 2 empty, 3 taken.
_CLOSE_LUA = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return {0, {}} end
if redis.call('EXISTS', KEYS[3]) == 1 then return {1, {}} end
local items = redis.call('HGETALL', KEYS[2])
redis.call('DEL', KEYS[1])
if #items == 0 then return {2, {}} end
redis.call('DEL', KEYS[2])
return {3, items}
"""

# Снятие замка только владельцем токена.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


async def _acquire_lock(redis: Redis, session_id: uuid.UUID, token: str) -> bool:
    ok = await redis.set(lock_key(session_id), token, nx=True, px=get_turn_settings().turn_lock_ms)
    return bool(ok)


async def _release_lock(redis: Redis, session_id: uuid.UUID, token: str) -> None:
    await redis.eval(_RELEASE_LUA, 1, lock_key(session_id), token)


async def _mark_window_done(redis: Redis, session_id: uuid.UUID, window_id: str) -> bool:
    """Маркер обработанного окна: дубль задачи dm_turn того же окна — no-op."""
    ok = await redis.set(_done_key(session_id, window_id), "1", nx=True, ex=86_400)
    return bool(ok)


async def _player_count(sessions: Sessions, session_id: uuid.UUID) -> int:
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(SeatRow)
            .where(SeatRow.session_id == session_id, SeatRow.role == "player")
        )
    return int(count or 0)


class SubmitResult(BaseModel):
    window_id: str
    pending: int
    turn_started: bool


async def submit_action(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, actor_id: str, text: str
) -> SubmitResult:
    """Действие в окно сбора; первое действие окна планирует таймаут-закрытие."""
    settings = get_turn_settings()
    raw = await redis.eval(
        _SUBMIT_LUA,
        2,
        pending_key(session_id),
        window_key(session_id),
        actor_id,
        text,
        uuid.uuid4().hex,
    )
    created, window_raw, count = cast(list[Any], raw)
    window_id = _decode(window_raw)
    if int(created) == 1:
        from ai_dnd.tasks import close_action_window

        await close_action_window.kiq(
            str(session_id), window_id, float(settings.turn_window_seconds)
        )
    turn_started = False
    players = await _player_count(sessions, session_id)
    if players > 0 and int(count) >= players:
        status, _ = await try_close_and_dispatch(sessions, redis, session_id, window_id)
        turn_started = status == "started"
    return SubmitResult(window_id=window_id, pending=int(count), turn_started=turn_started)


async def try_close_and_dispatch(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, window_id: str
) -> tuple[str, list[dict[str, str]]]:
    """Закрыть окно и поставить dm_turn. Статусы: stale/locked/empty/started."""
    raw = await redis.eval(
        _CLOSE_LUA,
        3,
        window_key(session_id),
        pending_key(session_id),
        lock_key(session_id),
        window_id,
    )
    code, items = cast(list[Any], raw)
    status = {0: "stale", 1: "locked", 2: "empty", 3: "started"}[int(code)]
    actions: list[dict[str, str]] = []
    if status == "started":
        pairs = cast(list[Any], items)
        actions = [
            {"actor_id": _decode(pairs[i]), "text": _decode(pairs[i + 1])}
            for i in range(0, len(pairs), 2)
        ]
        from ai_dnd.tasks import dm_turn

        await dm_turn.kiq(str(session_id), actions, window_id)
    return status, actions


async def flush_pending(sessions: Sessions, redis: Redis, session_id: uuid.UUID) -> None:
    """После хода: действия, накопленные под замком, запускают следующий ход."""
    window_raw = await redis.get(window_key(session_id))
    if window_raw is None:
        return
    await try_close_and_dispatch(sessions, redis, session_id, _decode(window_raw))


# --- Сообщения комнаты о служебных остановках (ru/en по языку сессии) ---

_STOP_EN = {
    "insufficient_credits": (
        "Not enough credits for an AI turn. Free credits refresh tomorrow; "
        "the game between players continues."
    ),
    "no_payer": ("This session has no payer for AI calls. The game between players continues."),
    "session_daily_limit": (
        "This session's daily AI credit limit is exhausted. The AI rests until tomorrow; "
        "the game between players continues."
    ),
    "user_daily_limit": (
        "The host's daily AI credit limit is exhausted. The AI rests until tomorrow; "
        "the game between players continues."
    ),
    "user_monthly_limit": (
        "The host's monthly AI credit limit is exhausted. The AI rests until next month; "
        "the game between players continues."
    ),
    "global_daily_fuse": (
        "The platform's daily AI spending fuse tripped. The AI is temporarily off; "
        "the game between players continues."
    ),
    "global_monthly_fuse": (
        "The platform's monthly AI spending fuse tripped. The AI is temporarily off; "
        "the game between players continues."
    ),
}


def _stop_text(stop: BillingStop, language: str) -> str:
    if language == "ru":
        return stop.message
    return _STOP_EN.get(stop.code, f"The AI DM is paused ({stop.code}). Human play continues.")


_LLM_ERROR_TEXT = {
    "ru": "Ведущий не смог ответить. Отправьте действия ещё раз — игра продолжается.",
    "en": "The DM could not answer. Send the actions again — the game continues.",
}

_COMBAT_TEXT = {
    "ru": (
        "Идёт бой: выбирайте боевые действия на экране игрока. "
        "Свободные действия ведущий-ИИ разберёт после боя."
    ),
    "en": (
        "Combat is in progress: choose combat actions on the player screen. "
        "The AI DM will handle free actions after the fight."
    ),
}


async def _system_narration(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, text: str
) -> None:
    """Публичное событие Narrated с служебным сообщением: комната остаётся играбельной."""
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None:
        return
    event = Narrated(text=text)
    state = apply_events(SessionState.model_validate(row.state), [event])
    journal = BroadcastJournal(DbJournal(sessions, session_id), redis, sessions)
    await journal.append([event], state)


# --- Сборка GameMaster как в console._play, плюс биллинг и стриминг ---


class _RoomGateway:
    """Адаптер над BillingGuard: плательщик сессии + потоковый рассказ в комнату.

    complete("narration") потребляется как stream: каждый TextDelta публикуется
    эфемерным chunk-сообщением ДО того, как master закоммитит финальный Narrated.
    CallContext каждого вызова получает session_id и user_id плательщика (host):
    GameMaster и build_summary передают контекст без user_id или не передают вовсе.
    """

    def __init__(
        self,
        inner: BillingGuard,
        redis: Redis,
        session_id: uuid.UUID,
        payer_id: uuid.UUID | None,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._session_id = session_id
        self._payer_id = payer_id

    @property
    def config(self) -> LLMConfig:
        return self._inner.config

    def _context(self) -> CallContext:
        return CallContext(session_id=self._session_id, user_id=self._payer_id)

    async def complete(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: ToolChoice | None = None,
        json_output: bool = False,
        context: CallContext | None = None,
    ) -> LLMResponse:
        if task == "narration" and not tools and not json_output:
            response: LLMResponse | None = None
            async for event in self._inner.stream(task, messages, context=self._context()):
                if isinstance(event, TextDelta):
                    await publish_chunk(self._redis, self._session_id, event.text)
                elif isinstance(event, StreamEnd):
                    response = event.response
            if response is None:
                raise LLMError("empty_stream")
            return response
        return await self._inner.complete(
            task,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            context=self._context(),
        )


async def _build_master(
    sessions: Sessions,
    redis: Redis,
    settings: Settings,
    row: GameSessionRow,
    version_id: uuid.UUID,
    *,
    fake: bool,
) -> GameMaster:
    content = await load_game_content_from_db(sessions, version_id, settings.content_dir)
    setup = SessionSetup.model_validate(row.setup)
    state = SessionState.model_validate(row.state)
    recorder = CompositeRecorder(MemoryRecorder(), DbRecorder(sessions))
    gateway = build_gateway(settings, recorder, fake=fake)
    guard = BillingGuard(gateway, CreditLedger(sessions), BillingLimits(sessions))
    room = _RoomGateway(guard, redis, row.id, row.host_id)
    journal = BroadcastJournal(DbJournal(sessions, row.id), redis, sessions)
    searcher = LoreSearcher(
        sessions,
        version_id,
        Embedder(settings.embedding_model, settings.embedding_dim),
        row.language,
    )
    # Адаптер повторяет публичный API LLMGateway, который использует GameMaster.
    return GameMaster(content, setup, state, cast(LLMGateway, room), journal, SystemRng(), searcher)


async def _grant_free(sessions: Sessions, host_id: uuid.UUID | None) -> None:
    if host_id is not None:
        await CreditLedger(sessions).grant_daily_free(host_id)


async def run_dm_turn(
    sessions: Sessions,
    redis: Redis,
    settings: Settings,
    session_id: uuid.UUID,
    actions: list[dict[str, str]],
    window_id: str,
    *,
    fake: bool,
) -> str:
    """Ход AI DM под замком комнаты; вызывается задачей воркера dm_turn."""
    token = uuid.uuid4().hex
    if not await _acquire_lock(redis, session_id, token):
        return "locked"
    language = "ru"
    try:
        if not await _mark_window_done(redis, session_id, window_id):
            return "duplicate"
        async with sessions() as db:
            row = await db.get(GameSessionRow, session_id)
        if row is None:
            return "session_not_found"
        language = row.language
        if row.dm_mode != "ai":
            return "not_ai"
        if row.scenario_version_id is None:
            return "no_scenario"
        if not actions:
            return "empty"
        await _grant_free(sessions, row.host_id)
        master = await _build_master(
            sessions, redis, settings, row, row.scenario_version_id, fake=fake
        )
        if master.in_combat:
            await _system_narration(
                sessions, redis, session_id, _COMBAT_TEXT.get(language, _COMBAT_TEXT["en"])
            )
            # Очередь могла остаться на существе после сбоя прошлого хода — доводим
            # её до human-актёра или конца боя из сохранённого состояния.
            await _advance_combat(sessions, redis, session_id, master, language)
            return "combat_pending"
        inputs = [PlayerInput(actor_id=a["actor_id"], text=a["text"]) for a in actions]
        # Chunk-и уже опубликованы адаптером, события идут через BroadcastJournal;
        # ToolUsed/ModelCalled в комнату не вещаются.
        async for _ in master.play_turn(inputs):
            pass
        if master.in_combat:
            # Бой начался этим ходом: если инициативу выиграло существо, очередь
            # сразу доходит до human-актёра — иначе игрок не может дать команду (G1).
            await _advance_combat(sessions, redis, session_id, master, language)
        return "ok"
    except BillingStop as stop:
        await _system_narration(sessions, redis, session_id, _stop_text(stop, language))
        return "billing_stop"
    except LLMError:
        await _system_narration(
            sessions, redis, session_id, _LLM_ERROR_TEXT.get(language, _LLM_ERROR_TEXT["en"])
        )
        return "llm_error"
    finally:
        await _release_lock(redis, session_id, token)
        await flush_pending(sessions, redis, session_id)
        await service.sync_knockout_mutes(sessions, redis, session_id)


async def run_dm_open(
    sessions: Sessions,
    redis: Redis,
    settings: Settings,
    session_id: uuid.UUID,
    *,
    fake: bool,
) -> str:
    """Первый рассказ ведущего при создании ai-сессии; повторный запуск — no-op."""
    token = uuid.uuid4().hex
    if not await _acquire_lock(redis, session_id, token):
        return "locked"
    language = "ru"
    try:
        async with sessions() as db:
            row = await db.get(GameSessionRow, session_id)
        if row is None:
            return "session_not_found"
        language = row.language
        if row.dm_mode != "ai":
            return "not_ai"
        if row.scenario_version_id is None:
            return "no_scenario"
        # Открытие уже было: в журнале есть рассказ (join-события могли прийти раньше).
        async with sessions() as db:
            narrated = await db.scalar(
                select(GameEventRow.id)
                .where(GameEventRow.session_id == session_id, GameEventRow.type == "Narrated")
                .limit(1)
            )
        if narrated is not None:
            return "already_open"
        await _grant_free(sessions, row.host_id)
        master = await _build_master(
            sessions, redis, settings, row, row.scenario_version_id, fake=fake
        )
        async for _ in master.open_session():
            pass
        return "ok"
    except BillingStop as stop:
        await _system_narration(sessions, redis, session_id, _stop_text(stop, language))
        return "billing_stop"
    except LLMError:
        await _system_narration(
            sessions, redis, session_id, _LLM_ERROR_TEXT.get(language, _LLM_ERROR_TEXT["en"])
        )
        return "llm_error"
    finally:
        await _release_lock(redis, session_id, token)
        await flush_pending(sessions, redis, session_id)


# --- Web-бой (3.4): варианты чистым движком, шаг игрока в воркере ---


async def _player_narration(
    sessions: Sessions, redis: Redis, session_id: uuid.UUID, actor_id: str, text: str
) -> None:
    """Narrated только одному игроку (и ведущему): личный ответ о боевом действии."""
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None:
        return
    event = Narrated(text=text, visibility=for_player(actor_id))
    state = apply_events(SessionState.model_validate(row.state), [event])
    journal = BroadcastJournal(DbJournal(sessions, session_id), redis, sessions)
    await journal.append([event], state)


_ACTION_ERROR_TEXT = {
    "ru": "Действие сейчас недоступно ({code}). Выберите другое — ход остаётся за вами.",
    "en": "That action is not available now ({code}). Choose another one — your turn continues.",
}

# Человекочитаемые варианты боя — как консольный _describe (console.py).
_OPTION_TEXTS = {
    "ru": {
        "attack": "Атаковать: {attack} → {target} (КД {ac}, хиты {hp})",
        "flee": "Сбежать из боя",
        "end_turn": "Закончить ход",
        "hide": "Спрятаться",
        "protect": "Прикрыть союзника: {target} (хиты {hp})",
    },
    "en": {
        "attack": "Attack: {attack} → {target} (AC {ac}, HP {hp})",
        "flee": "Flee the fight",
        "end_turn": "End turn",
        "hide": "Hide",
        "protect": "Protect ally: {target} (HP {hp})",
    },
}


def _describe_option(action: Action, state: GameState, language: str) -> tuple[str, str | None]:
    """(label, target_id) варианта: атака показывает оружие, цель, КД и хиты."""
    texts = _OPTION_TEXTS.get(language, _OPTION_TEXTS["en"])
    if isinstance(action, AttackAction):
        target = state.actors[action.target_id]
        attack = state.actors[action.attacker_id].attacks[action.attack]
        hp = target.hp
        label = texts["attack"].format(
            attack=attack.name,
            target=target.name,
            ac=target.ac,
            hp=f"{hp.current}/{hp.max}" if hp else "—",
        )
        return label, action.target_id
    if isinstance(action, FleeAction):
        return texts["flee"], None
    if isinstance(action, EndTurnAction):
        return texts["end_turn"], None
    if isinstance(action, HideAction):
        return texts["hide"], None
    if isinstance(action, ProtectAction):
        target = state.actors[action.target_id]
        hp = target.hp
        label = texts["protect"].format(
            target=target.name, hp=f"{hp.current}/{hp.max}" if hp else "—"
        )
        return label, action.target_id
    return action.type, None


async def _engine_for(sessions: Sessions, row: GameSessionRow) -> RulesEngine:
    """Движок из пиннутого набора правил сессии — без контента сценария и LLM."""
    ruleset_row: RulesetRow | None = None
    if row.ruleset_id is not None:
        async with sessions() as db:
            ruleset_row = await db.get(RulesetRow, row.ruleset_id)
    if ruleset_row is None:
        raise HTTPException(409, "session_without_ruleset")
    return RulesEngine(Ruleset.model_validate(ruleset_row.data))


async def _has_human_player_seat(sessions: Sessions, session_id: uuid.UUID, actor_id: str) -> bool:
    async with sessions() as db:
        seat_id = await db.scalar(
            select(SeatRow.id).where(
                SeatRow.session_id == session_id,
                SeatRow.actor_id == actor_id,
                SeatRow.role == "player",
                SeatRow.controller == "human",
            )
        )
    return seat_id is not None


async def _drain_combat_narration(master: GameMaster) -> None:
    """Рассказ накопленного раунда (стрим chunk-ов уже публикует _RoomGateway)."""
    while master.round_finished:
        async for _ in master.narrate_combat():
            pass


async def _advance_combat(
    sessions: Sessions,
    redis: Redis,
    session_id: uuid.UUID,
    master: GameMaster,
    language: str,
) -> str:
    """Общий цикл автоходов как console._combat: ходы существ, рассказ раунда,
    авто-действие при единственном варианте. Стоп — party-актёр с human-местом
    (ждём HTTP), конец боя (финальный рассказ после CombatEnded обязателен)
    или ошибка действия. Работает и из сохранённого состояния: состав и очередь
    боя восстанавливаются в master из row.state.
    """
    while True:
        await _drain_combat_narration(master)
        if not master.in_combat:
            return "ok"
        await master.run_creature_turns()
        await _drain_combat_narration(master)
        if not master.in_combat:
            return "ok"
        current = master.current_combatant
        if current is None or not master.is_party(current):
            # Ходы существ останавливаются только на party-актёре или конце боя.
            return "ok"
        current_options = master.combat_options(current)
        if len(current_options) == 1:
            # Как консоль: единственное доступное действие выполняется само.
            try:
                await master.act(current_options[0])
            except ActionError as error:
                await _player_narration(
                    sessions,
                    redis,
                    session_id,
                    current,
                    _ACTION_ERROR_TEXT.get(language, _ACTION_ERROR_TEXT["en"]).format(
                        code=error.code
                    ),
                )
                return "action_error"
            continue
        if await _has_human_player_seat(sessions, session_id, current):
            return "waiting_player"
        # Party-актёр без human-места (спутник под управлением ИИ): тактика движка.
        try:
            await master.act(choose_action(master.engine, master.state.rules, current, SystemRng()))
        except ActionError as error:
            await _player_narration(
                sessions,
                redis,
                session_id,
                current,
                _ACTION_ERROR_TEXT.get(language, _ACTION_ERROR_TEXT["en"]).format(code=error.code),
            )
            return "action_error"


async def run_combat_step(
    sessions: Sessions,
    redis: Redis,
    settings: Settings,
    session_id: uuid.UUID,
    actor_id: str,
    option_index: int,
    *,
    fake: bool,
) -> str:
    """Боевой шаг игрока под замком комнаты; вызывается задачей воркера combat_step.

    Цикл как console._combat: действие игрока, ходы существ, рассказ раунда;
    единственный вариант party-актёра выполняется автоматически. Стоп — когда
    очередь дошла до party-актёра с human-местом (ждём HTTP) или бой окончен
    (финальный рассказ после CombatEnded обязателен).
    """
    token = uuid.uuid4().hex
    if not await _acquire_lock(redis, session_id, token):
        return "locked"
    language = "ru"
    try:
        async with sessions() as db:
            row = await db.get(GameSessionRow, session_id)
        if row is None:
            return "session_not_found"
        language = row.language
        if row.dm_mode != "ai":
            return "not_ai"
        if row.scenario_version_id is None:
            return "no_scenario"
        combat = SessionState.model_validate(row.state).rules.combat
        if combat is None:
            return "not_in_combat"
        if combat.current != actor_id:
            return "not_your_turn"
        await _grant_free(sessions, row.host_id)
        master = await _build_master(
            sessions, redis, settings, row, row.scenario_version_id, fake=fake
        )
        if not master.is_party(actor_id):
            return "not_party"
        options = master.combat_options(actor_id)
        if option_index < 0 or option_index >= len(options):
            return "invalid_option"
        try:
            await master.act(options[option_index])
        except ActionError as error:
            await _player_narration(
                sessions,
                redis,
                session_id,
                actor_id,
                _ACTION_ERROR_TEXT.get(language, _ACTION_ERROR_TEXT["en"]).format(code=error.code),
            )
            return "action_error"
        return await _advance_combat(sessions, redis, session_id, master, language)
    except BillingStop as stop:
        await _system_narration(sessions, redis, session_id, _stop_text(stop, language))
        return "billing_stop"
    except LLMError:
        await _system_narration(
            sessions, redis, session_id, _LLM_ERROR_TEXT.get(language, _LLM_ERROR_TEXT["en"])
        )
        return "llm_error"
    finally:
        await _release_lock(redis, session_id, token)
        await flush_pending(sessions, redis, session_id)
        await service.sync_knockout_mutes(sessions, redis, session_id)


# --- HTTP: действия игроков и явный триггер хода ---


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionRequest(_Schema):
    text: str = Field(min_length=1, max_length=2000)


class ActionResponse(_Schema):
    mode: str
    window_id: str | None = None
    pending: int | None = None
    turn_started: bool = False
    seq: int | None = None


class TurnTriggerResponse(_Schema):
    window_id: str
    actions: int


class CombatOptionOut(_Schema):
    index: int
    label: str
    kind: str
    target_id: str | None = None


class CombatOptionsResponse(_Schema):
    in_combat: bool
    current_actor_id: str | None
    your_turn: bool
    options: list[CombatOptionOut]


class CombatActionRequest(_Schema):
    option_index: int = Field(ge=0)


class CombatActionResponse(_Schema):
    accepted: bool = True


async def _human_action(
    sessions: Sessions, redis: Redis, row: GameSessionRow, actor_id: str, text: str
) -> int:
    event = PlayerActed(actor_id=actor_id, text=text)
    state = apply_events(SessionState.model_validate(row.state), [event])
    journal = BroadcastJournal(DbJournal(sessions, row.id), redis, sessions)
    await journal.append([event], state)
    async with sessions() as db:
        last_seq = await db.scalar(
            select(GameSessionRow.last_seq).where(GameSessionRow.id == row.id)
        )
    return int(last_seq or 0)


def build_turns_router(get_current_user: GetCurrentUser) -> APIRouter:
    """Роутер хода; аутентификация — seam GetCurrentUser (как realtime.router)."""
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    @router.post("/{session_id}/actions", response_model=ActionResponse)
    async def post_action(
        session_id: uuid.UUID,
        body: ActionRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> ActionResponse:
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        if row.status != "active":
            raise HTTPException(409, {"code": "session_stopped"})
        # Только player: dm и будущие spectator (3.8) действий не имеют.
        if seat.role != "player":
            raise HTTPException(403, "players_only")
        # Мут блокирует только текст; боевые действия остаются (иначе бой встаёт).
        if seat.muted:
            raise HTTPException(403, "muted")
        # Без сознания/мёртв: команды недоступны, чат вне игры остаётся открытым.
        state = SessionState.model_validate(row.state)
        actor = state.rules.actors.get(seat.actor_id)
        if actor is not None and (
            actor.dead
            or (actor.hp is not None and actor.hp.current <= 0)
            or incapacitated_by((await _engine_for(sessions, row)).ruleset, actor) is not None
        ):
            raise HTTPException(403, {"code": "incapacitated"})
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "empty_action")
        if row.dm_mode == "human":
            seq = await _human_action(sessions, resources.redis, row, seat.actor_id, text)
            return ActionResponse(mode="human", seq=seq)
        result = await submit_action(sessions, resources.redis, session_id, seat.actor_id, text)
        return ActionResponse(
            mode="ai",
            window_id=result.window_id,
            pending=result.pending,
            turn_started=result.turn_started,
        )

    @router.post("/{session_id}/turn", response_model=TurnTriggerResponse, status_code=202)
    async def trigger_turn(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> TurnTriggerResponse:
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        if row.status != "active":
            raise HTTPException(409, {"code": "session_stopped"})
        if seat.role not in ("dm", "player"):
            raise HTTPException(403, "dm_or_player_only")
        if row.dm_mode != "ai":
            raise HTTPException(409, "not_ai_session")
        redis = resources.redis
        if await redis.exists(lock_key(session_id)):
            raise HTTPException(409, "turn_in_progress")
        window_raw = await redis.get(window_key(session_id))
        if window_raw is None:
            raise HTTPException(409, "no_pending_actions")
        window_id = _decode(window_raw)
        status, actions = await try_close_and_dispatch(sessions, redis, session_id, window_id)
        if status == "locked":
            raise HTTPException(409, "turn_in_progress")
        if status != "started":
            raise HTTPException(409, "no_pending_actions")
        return TurnTriggerResponse(window_id=window_id, actions=len(actions))

    @router.get("/{session_id}/combat-options", response_model=CombatOptionsResponse)
    async def get_combat_options(
        session_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CombatOptionsResponse:
        """Варианты боя чистым движком из state.rules — без GameMaster и LLM."""
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        state = SessionState.model_validate(row.state)
        combat = state.rules.combat
        options: list[CombatOptionOut] = []
        if combat is not None:
            engine = await _engine_for(sessions, row)
            actions = engine.available_actions(state.rules, seat.actor_id)
            for index, action in enumerate(actions):
                label, target_id = _describe_option(action, state.rules, row.language)
                options.append(
                    CombatOptionOut(index=index, label=label, kind=action.type, target_id=target_id)
                )
        return CombatOptionsResponse(
            in_combat=combat is not None,
            current_actor_id=combat.current if combat is not None else None,
            your_turn=(
                combat is not None and combat.current == seat.actor_id and seat.role == "player"
            ),
            options=options,
        )

    @router.post(
        "/{session_id}/combat-actions", response_model=CombatActionResponse, status_code=202
    )
    async def post_combat_action(
        session_id: uuid.UUID,
        body: CombatActionRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CombatActionResponse:
        sessions = resources.db_sessions
        row, seat = await _session_and_seat(sessions, session_id, user)
        if row.status != "active":
            raise HTTPException(409, {"code": "session_stopped"})
        if seat.role != "player":
            raise HTTPException(403, "players_only")
        if row.dm_mode != "ai":
            raise HTTPException(409, "not_ai_session")
        state = SessionState.model_validate(row.state)
        combat = state.rules.combat
        if combat is None:
            raise HTTPException(409, "not_in_combat")
        if combat.current != seat.actor_id:
            raise HTTPException(409, "not_your_turn")
        if await resources.redis.exists(lock_key(session_id)):
            raise HTTPException(409, "turn_in_progress")
        # Индекс валидируется против свежих вариантов; воркер перепроверит под замком.
        engine = await _engine_for(sessions, row)
        options = engine.available_actions(state.rules, seat.actor_id)
        if body.option_index >= len(options):
            raise HTTPException(422, "invalid_option")
        from ai_dnd.tasks import combat_step

        await combat_step.kiq(str(session_id), seat.actor_id, body.option_index)
        return CombatActionResponse()

    return router


async def _session_and_seat(
    sessions: Sessions, session_id: uuid.UUID, user: CurrentUser
) -> tuple[GameSessionRow, SeatRow]:
    async with sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session_not_found")
    seat = await service.find_seat(sessions, session_id, user.user_id)
    if seat is None:
        raise HTTPException(403, "no_seat")
    return row, seat
