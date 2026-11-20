"""Регрессия G1: бой не встаёт, когда инициативу выигрывает существо.

Офлайн-проба с реальным движком и сценарием: текстовый ход начинает бой,
предопределённая инициатива ставит гоблинов первыми. Общий цикл автоходов
(``turns._advance_combat``) обязан довести очередь до human-актёра сразу после
начала боя, из сохранённого состояния и после боевого шага игрока; конец боя
обязательно завершается финальным рассказом. БД/Redis/модель подменены: журнал
пишет снимок в строку сессии как DbJournal, провайдер — FakeProvider.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any, cast

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from starlette.requests import HTTPConnection

from ai_dnd import tasks
from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.providers.fake import FakeProvider
from ai_dnd.ai.recorder import MemoryRecorder
from ai_dnd.config import Settings
from ai_dnd.game.content import GameContent, load_party, new_session_state
from ai_dnd.game.events import JournalEvent, Narrated, PlayerActed
from ai_dnd.game.master import GameMaster
from ai_dnd.game.models import GameSessionRow
from ai_dnd.game.session import SessionSetup, SessionState
from ai_dnd.realtime import service, turns
from ai_dnd.realtime.auth import CurrentUser
from ai_dnd.realtime.models import SeatRow
from ai_dnd.resources import Resources, get_resources
from ai_dnd.rules.actions import StartCombatAction
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.events import AttackResolved
from ai_dnd.rules.rng import ScriptedRng, SeededRng
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import Actor, Resource

SCENARIO = "shadow-over-millford"
PARTY = ("fighter", "rogue", "cleric")


class _ScriptedThenSeeded:
    """Сначала заданные броски (инициатива, атака), затем повторяемый генератор."""

    def __init__(self, values: Sequence[int], seed: int) -> None:
        self._scripted = ScriptedRng(values)
        self._seeded = SeededRng(seed)

    def randint(self, low: int, high: int) -> int:
        if self._scripted.remaining > 0:
            return self._scripted.randint(low, high)
        return self._seeded.randint(low, high)


class _RowJournal:
    """Журнал в строку сессии: снимок персистится как DbJournal, события в памяти."""

    def __init__(self, row: GameSessionRow) -> None:
        self._row = row
        self.events: list[JournalEvent] = []

    @property
    def session_id(self) -> uuid.UUID:
        return self._row.id

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        if not events:
            return
        self.events.extend(events)
        self._row.last_seq += len(events)
        self._row.state = state.model_dump(mode="json")


class _FakeDb:
    def __init__(self, row: GameSessionRow) -> None:
        self._row = row

    async def get(self, model: type[Any], pk: uuid.UUID) -> GameSessionRow | None:
        return self._row if model is GameSessionRow else None


class _FakeSessionCtx:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class _FakeSessions:
    def __init__(self, row: GameSessionRow) -> None:
        self._row = row

    def __call__(self) -> _FakeSessionCtx:
        return _FakeSessionCtx(_FakeDb(self._row))


class _FakeRedis:
    """Замок комнаты и маркер окна: set/get/exists и снятие замка по токену."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self._data:
            return None
        self._data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def exists(self, key: str) -> int:
        return int(key in self._data)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        # Единственный скрипт на этих путях — _RELEASE_LUA (снятие замка владельцем).
        key, token = str(args[0]), str(args[1])
        if self._data.get(key) == token:
            del self._data[key]
            return 1
        return 0


class _StepTask:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def kiq(self, *args: Any) -> None:
        self.calls.append(args)


@dataclass
class _Room:
    row: GameSessionRow
    redis: _FakeRedis
    sessions: turns.Sessions
    journal: _RowJournal
    settings: Settings
    system_texts: list[str]
    player_texts: list[tuple[str, str]]

    @property
    def state(self) -> SessionState:
        return SessionState.model_validate(self.row.state)


def _setup_and_state(
    content_dir: Path, game_content: GameContent
) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, game_content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    state = new_session_state(game_content, actors)
    # Сцена с засадой: текстовое действие «атакую» начинает goblin_ambush.
    return setup, state.model_copy(update={"scene_id": "mill_road"})


def _make_room(
    monkeypatch: pytest.MonkeyPatch,
    *,
    game_content: GameContent,
    llm_config: LLMConfig,
    setup: SessionSetup,
    state: SessionState,
    rng: _ScriptedThenSeeded,
) -> _Room:
    row = GameSessionRow(
        id=uuid.uuid4(),
        scenario=SCENARIO,
        language="ru",
        dm_mode="ai",
        status="active",
        setup=setup.model_dump(mode="json"),
        state=state.model_dump(mode="json"),
        scenario_version_id=uuid.uuid4(),
        host_id=None,
        last_seq=0,
    )
    journal = _RowJournal(row)
    provider = FakeProvider()
    gateway = LLMGateway(
        llm_config, dict.fromkeys(llm_config.providers, provider), MemoryRecorder()
    )
    humans = set(setup.party)
    system_texts: list[str] = []
    player_texts: list[tuple[str, str]] = []

    async def build_master(
        sessions: Any,
        redis: Any,
        settings: Settings,
        row_arg: GameSessionRow,
        version_id: uuid.UUID,
        *,
        fake: bool,
    ) -> GameMaster:
        # Свежий ведущий из сохранённого снимка — как настоящий _build_master.
        return GameMaster(
            game_content,
            SessionSetup.model_validate(row_arg.setup),
            SessionState.model_validate(row_arg.state),
            gateway,
            journal,
            rng,
            None,
        )

    async def grant_free(sessions: Any, host_id: uuid.UUID | None) -> None:
        return None

    async def system_narration(sessions: Any, redis: Any, session_id: uuid.UUID, text: str) -> None:
        system_texts.append(text)

    async def player_narration(
        sessions: Any, redis: Any, session_id: uuid.UUID, actor_id: str, text: str
    ) -> None:
        player_texts.append((actor_id, text))

    async def has_human_seat(sessions: Any, session_id: uuid.UUID, actor_id: str) -> bool:
        return actor_id in humans

    async def noop(sessions: Any, redis: Any, session_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(turns, "_build_master", build_master)
    monkeypatch.setattr(turns, "_grant_free", grant_free)
    monkeypatch.setattr(turns, "_system_narration", system_narration)
    monkeypatch.setattr(turns, "_player_narration", player_narration)
    monkeypatch.setattr(turns, "_has_human_player_seat", has_human_seat)
    monkeypatch.setattr(turns, "flush_pending", noop)
    monkeypatch.setattr(service, "sync_knockout_mutes", noop)
    return _Room(
        row=row,
        redis=_FakeRedis(),
        sessions=cast(turns.Sessions, _FakeSessions(row)),
        journal=journal,
        settings=Settings(_env_file=None),
        system_texts=system_texts,
        player_texts=player_texts,
    )


def _http_app(
    monkeypatch: pytest.MonkeyPatch, room: _Room, seat: SeatRow, ruleset: Ruleset
) -> tuple[FastAPI, _StepTask]:
    async def current_user(connection: HTTPConnection) -> CurrentUser:
        return CurrentUser(user_id=seat.user_id or uuid.uuid4())

    async def session_and_seat(
        sessions: Any, session_id: uuid.UUID, user: CurrentUser
    ) -> tuple[GameSessionRow, SeatRow]:
        return room.row, seat

    async def engine_for(sessions: Any, row: GameSessionRow) -> RulesEngine:
        return RulesEngine(ruleset)

    step = _StepTask()
    monkeypatch.setattr(turns, "_session_and_seat", session_and_seat)
    monkeypatch.setattr(turns, "_engine_for", engine_for)
    monkeypatch.setattr(tasks, "combat_step", step)
    app = FastAPI()
    app.include_router(turns.build_turns_router(current_user))
    resources = cast(Resources, SimpleNamespace(db_sessions=room.sessions, redis=room.redis))
    app.dependency_overrides[get_resources] = lambda: resources
    return app, step


def _goblin_attacks(room: _Room) -> int:
    return sum(
        1
        for event in room.journal.events
        if isinstance(event, AttackResolved) and event.attacker_id.startswith("goblin")
    )


async def _start_combat_by_text(room: _Room) -> str:
    """Текстовый ход воина в сцене засады; возвращает результат run_dm_turn."""
    return await turns.run_dm_turn(
        room.sessions,
        cast(Redis, room.redis),
        room.settings,
        room.row.id,
        [{"actor_id": "fighter", "text": "Атакую гоблинов из засады!"}],
        f"window-{uuid.uuid4().hex}",
        fake=True,
    )


async def test_text_turn_starting_combat_advances_past_npc(
    monkeypatch: pytest.MonkeyPatch,
    content_dir: Path,
    game_content: GameContent,
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
) -> None:
    setup, state = _setup_and_state(content_dir, game_content)
    # Инициатива: отряд бросает 2, четверо гоблинов — 18. Первыми ходят существа.
    rng = _ScriptedThenSeeded([2, 2, 2, 18, 18, 18, 18], seed=7)
    room = _make_room(
        monkeypatch,
        game_content=game_content,
        llm_config=llm_config,
        setup=setup,
        state=state,
        rng=rng,
    )

    result = await _start_combat_by_text(room)

    assert result == "ok"
    combat = room.state.rules.combat
    assert combat is not None
    assert combat.order[0].startswith("goblin")
    # Очередь дошла до живого игрока, а не осталась на гоблине.
    assert combat.current in PARTY
    assert _goblin_attacks(room) > 0

    seat = SeatRow(
        session_id=room.row.id,
        role="player",
        controller="human",
        user_id=uuid.uuid4(),
        actor_id=combat.current,
        muted=False,
    )
    app, step = _http_app(monkeypatch, room, seat, srd5_ruleset)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        options = await client.get(f"/api/sessions/{room.row.id}/combat-options")
        response = await client.post(
            f"/api/sessions/{room.row.id}/combat-actions", json={"option_index": 0}
        )

    assert options.status_code == 200
    assert options.json()["your_turn"] is True
    # Команда принята (202), а не 409 not_your_turn; шаг поставлен воркеру.
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert step.calls == [(str(room.row.id), combat.current, 0)]


async def test_combat_step_still_stops_at_human_turn(
    monkeypatch: pytest.MonkeyPatch,
    content_dir: Path,
    game_content: GameContent,
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
) -> None:
    setup, state = _setup_and_state(content_dir, game_content)
    rng = _ScriptedThenSeeded([2, 2, 2, 18, 18, 18, 18], seed=7)
    room = _make_room(
        monkeypatch,
        game_content=game_content,
        llm_config=llm_config,
        setup=setup,
        state=state,
        rng=rng,
    )
    assert await _start_combat_by_text(room) == "ok"
    engine = RulesEngine(srd5_ruleset)
    attacks_before = _goblin_attacks(room)

    # Каждый human-актёр заканчивает ход; после последнего рассказ раунда и
    # ходы существ второго раунда выполняются тем же циклом, что и раньше.
    for _ in PARTY:
        combat = room.state.rules.combat
        assert combat is not None
        assert combat.current in PARTY
        options = engine.available_actions(room.state.rules, combat.current)
        result = await turns.run_combat_step(
            room.sessions,
            cast(Redis, room.redis),
            room.settings,
            room.row.id,
            combat.current,
            len(options) - 1,
            fake=True,
        )
        assert result in {"ok", "waiting_player"}
        if result == "ok":
            break

    combat = room.state.rules.combat
    assert combat is not None
    assert combat.current in PARTY
    assert combat.round == 2
    assert _goblin_attacks(room) > attacks_before


async def test_combat_end_emits_final_narration(
    monkeypatch: pytest.MonkeyPatch,
    content_dir: Path,
    game_content: GameContent,
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    goblin: Actor,
) -> None:
    setup, state = _setup_and_state(content_dir, game_content)
    # Инициатива: воин 20 (+1) первым, затем отряд и гоблин 2; атака 19, урон 6.
    rng = _ScriptedThenSeeded([20, 15, 14, 2, 19, 6], seed=3)
    engine = RulesEngine(srd5_ruleset)
    weak = goblin.model_copy(
        update={"resources": {**goblin.resources, "hp": Resource(current=1, max=7)}}
    )
    rules = state.rules.with_actor(weak)
    started = engine.resolve(
        rules, StartCombatAction(sides={"party": PARTY, "enemies": ("goblin",)}), rng
    )
    state = state.model_copy(update={"rules": apply_all(rules, started)})
    room = _make_room(
        monkeypatch,
        game_content=game_content,
        llm_config=llm_config,
        setup=setup,
        state=state,
        rng=rng,
    )
    combat = room.state.rules.combat
    assert combat is not None
    assert combat.current == "fighter"

    result = await turns.run_combat_step(
        room.sessions,
        cast(Redis, room.redis),
        room.settings,
        room.row.id,
        "fighter",
        0,
        fake=True,
    )

    assert result == "ok"
    assert room.state.rules.combat is None
    types = [event.type for event in room.journal.events]
    assert "CombatEnded" in types
    # Финальный рассказ обязателен после CombatEnded.
    assert "Narrated" in types[types.index("CombatEnded") + 1 :]
    assert any(isinstance(event, Narrated) for event in room.journal.events)


async def test_text_during_combat_resumes_npc_queue_from_saved_state(
    monkeypatch: pytest.MonkeyPatch,
    content_dir: Path,
    game_content: GameContent,
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    goblin: Actor,
) -> None:
    setup, state = _setup_and_state(content_dir, game_content)
    # Сохранённое состояние: бой уже начался (например, прошлый ход оборвался),
    # инициатива у гоблина — отряд бросает 2, гоблин 18.
    rng = _ScriptedThenSeeded([2, 2, 2, 18], seed=11)
    engine = RulesEngine(srd5_ruleset)
    rules = state.rules.with_actor(goblin)
    started = engine.resolve(
        rules, StartCombatAction(sides={"party": PARTY, "enemies": ("goblin",)}), rng
    )
    state = state.model_copy(update={"rules": apply_all(rules, started)})
    room = _make_room(
        monkeypatch,
        game_content=game_content,
        llm_config=llm_config,
        setup=setup,
        state=state,
        rng=rng,
    )
    combat = room.state.rules.combat
    assert combat is not None
    assert combat.current == "goblin"

    result = await turns.run_dm_turn(
        room.sessions,
        cast(Redis, room.redis),
        room.settings,
        room.row.id,
        [{"actor_id": "fighter", "text": "Осматриваюсь вокруг"}],
        f"window-{uuid.uuid4().hex}",
        fake=True,
    )

    assert result == "combat_pending"
    combat = room.state.rules.combat
    assert combat is not None
    assert combat.current in PARTY
    # Свободный текст в бою не исполняется, но очередь существ доведена до игрока.
    assert not any(isinstance(event, PlayerActed) for event in room.journal.events)
    assert room.system_texts
