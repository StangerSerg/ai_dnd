"""Ведущий-ИИ: оркестратор хода (архитектура, раздел 5.3).

Свободная игра — цикл с инструментами:

1. собрать контекст (сцена, отряд, действия игроков);
2. модель отвечает текстом и, если нужно, вызывает инструменты;
3. каждый вызов проверяет и выполняет код (``DmTools``), события пишутся в журнал,
   а модель получает исход;
4. модель продолжает — до финального рассказа без инструментов.

Бой ведёт движок: монстры ходят по своей тактике, игроки выбирают из списка
допустимых действий. Модель описывает каждый раунд одним вызовом.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.recorder import CallContext
from ai_dnd.ai.schemas import LLMResponse, Message, StreamEnd, TextDelta
from ai_dnd.game.content import GameContent
from ai_dnd.game.context import ActionLine, ContextBuilder
from ai_dnd.game.events import JournalEvent, Narrated, PlayerActed
from ai_dnd.game.journal import Journal
from ai_dnd.game.render import ModelLogRenderer
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.game.tools import DmTools, tool_result_text
from ai_dnd.rules.actions import Action
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.events import CombatEnded, RoundStarted
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.tactics import choose_action

# Сколько раз за ход модель может вызывать инструменты, прежде чем её попросят
# закончить рассказ без них.
MAX_TOOL_ROUNDS = 4
# Сколько прошлых ходов идёт в промпт дословно. Сводка старых — подэтап 2.4.
HISTORY_TURNS = 12


@dataclass(frozen=True, slots=True)
class PlayerInput:
    actor_id: str
    text: str


@dataclass(frozen=True, slots=True)
class NarrationChunk:
    text: str


@dataclass(frozen=True, slots=True)
class EventsHappened:
    events: tuple[JournalEvent, ...]


@dataclass(frozen=True, slots=True)
class ToolUsed:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelCalled:
    response: LLMResponse


TurnUpdate = NarrationChunk | EventsHappened | ToolUsed | ModelCalled


class GameMaster:
    def __init__(
        self,
        content: GameContent,
        setup: SessionSetup,
        state: SessionState,
        gateway: LLMGateway,
        journal: Journal,
        rng: Rng,
    ) -> None:
        self._content = content
        self._setup = setup
        self._state = state
        self._gateway = gateway
        self._journal = journal
        self._rng = rng
        self._engine = RulesEngine(content.ruleset)
        self._tools = DmTools(content, setup, self._engine, rng)
        self._context = ContextBuilder(content, setup)
        self._log_renderer = ModelLogRenderer(content)
        # Прошлые ходы: (как ход выглядит в истории, рассказ ведущего).
        self._history: list[tuple[str, str]] = []
        # События боя, которые модель ещё не описала.
        self._combat_log: list[JournalEvent] = []
        self._call_context = CallContext(session_id=journal.session_id)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def setup(self) -> SessionSetup:
        return self._setup

    @property
    def engine(self) -> RulesEngine:
        return self._engine

    # --- Свободная игра ---

    async def open_session(self) -> AsyncIterator[TurnUpdate]:
        """Первый рассказ ведущего: стартовая сцена."""
        message = self._context.turn_message(self._state, (), opening=True)
        async for update in self._dm_turn(message, self._context.history_entry(())):
            yield update

    async def play_turn(self, inputs: Sequence[PlayerInput]) -> AsyncIterator[TurnUpdate]:
        """Ход ведущего в ответ на действия игроков — одним вызовом на всех."""
        acted: list[JournalEvent] = [
            PlayerActed(actor_id=item.actor_id, text=item.text) for item in inputs
        ]
        await self._commit(acted)
        yield EventsHappened(tuple(acted))

        actions = [
            ActionLine(
                actor_id=item.actor_id,
                name=self._state.rules.actor(item.actor_id).name,
                text=item.text,
            )
            for item in inputs
        ]
        message = self._context.turn_message(self._state, actions)
        async for update in self._dm_turn(message, self._context.history_entry(actions)):
            yield update

    async def _dm_turn(self, message: str, history_entry: str) -> AsyncIterator[TurnUpdate]:
        messages = [
            Message.system(self._context.system_prompt),
            *self._history_messages(),
            Message.user(message),
        ]
        specs = self._tools.specs()
        parts: list[str] = []

        for round_number in range(MAX_TOOL_ROUNDS + 1):
            last_round = round_number == MAX_TOOL_ROUNDS
            response: LLMResponse | None = None
            text: list[str] = []
            if parts:
                # Новый кусок рассказа после инструментов — с нового абзаца.
                yield NarrationChunk("\n\n")
            async for event in self._gateway.stream(
                "dm_tools",
                messages,
                tools=specs,
                tool_choice="none" if last_round else None,
                context=self._call_context,
            ):
                if isinstance(event, TextDelta):
                    text.append(event.text)
                    yield NarrationChunk(event.text)
                elif isinstance(event, StreamEnd):
                    response = event.response
            if response is None:
                break
            yield ModelCalled(response)
            if "".join(text).strip():
                parts.append("".join(text).strip())
            if not response.tool_calls or last_round:
                break

            messages.append(response.as_message())
            for call in response.tool_calls:
                outcome = self._tools.execute(self._state, call)
                if outcome.events:
                    await self._commit(outcome.events)
                    yield EventsHappened(tuple(outcome.events))
                yield ToolUsed(call.name, call.arguments, outcome.result)
                messages.append(Message.tool(call, tool_result_text(outcome)))

        narration = "\n\n".join(parts)
        if narration:
            await self._commit([Narrated(text=narration)])
            self._history.append((history_entry, narration))
        # Бой мог начаться в этом ходе: его начало уже описано.
        self._combat_log.clear()

    # --- Бой ---

    @property
    def in_combat(self) -> bool:
        return self._state.rules.combat is not None

    @property
    def current_combatant(self) -> str | None:
        combat = self._state.rules.combat
        return combat.current if combat is not None else None

    def is_party(self, actor_id: str) -> bool:
        return actor_id in self._setup.party

    def combat_options(self, actor_id: str) -> list[Action]:
        return self._engine.available_actions(self._state.rules, actor_id)

    async def act(self, action: Action) -> list[JournalEvent]:
        """Действие в бою. Если его нельзя выполнить — ``ActionError`` от движка."""
        events: list[JournalEvent] = list(
            self._engine.resolve(self._state.rules, action, self._rng)
        )
        await self._commit(events)
        self._combat_log.extend(events)
        return events

    async def run_creature_turns(self) -> list[JournalEvent]:
        """Ходы существ по их тактике — пока не придёт очередь игрока или бой не кончится."""
        happened: list[JournalEvent] = []
        while self.in_combat and self._needs_narration_at(happened) is False:
            current = self.current_combatant
            if current is None or self.is_party(current):
                break
            action = choose_action(self._engine, self._state.rules, current, self._rng)
            happened.extend(await self.act(action))
        return happened

    @property
    def round_finished(self) -> bool:
        """Пора описать раунд: начался следующий или бой окончен."""
        return self._needs_narration_at(self._combat_log)

    async def narrate_combat(self) -> AsyncIterator[TurnUpdate]:
        """Описать накопленные события боя одним вызовом модели."""
        log = [
            line
            for event in self._combat_log
            if (line := self._log_renderer.line(event, self._state)) is not None
        ]
        ended = any(isinstance(event, CombatEnded) for event in self._combat_log)
        self._combat_log.clear()
        if not log:
            return
        message = self._context.combat_message(self._state, log, ended=ended)
        messages = [
            Message.system(self._context.system_prompt),
            *self._history_messages(),
            Message.user(message),
        ]
        text: list[str] = []
        async for event in self._gateway.stream("narration", messages, context=self._call_context):
            if isinstance(event, TextDelta):
                text.append(event.text)
                yield NarrationChunk(event.text)
            elif isinstance(event, StreamEnd):
                yield ModelCalled(event.response)
        narration = "".join(text).strip()
        if narration:
            await self._commit([Narrated(text=narration)])
            self._history.append(("(Combat round)", narration))

    # --- Внутреннее ---

    async def _commit(self, events: Sequence[JournalEvent]) -> None:
        if not events:
            return
        self._state = apply_events(self._state, events)
        await self._journal.append(events, self._state)

    def _history_messages(self) -> list[Message]:
        messages: list[Message] = []
        for entry, narration in self._history[-HISTORY_TURNS:]:
            messages.append(Message.user(entry))
            messages.append(Message(role="assistant", content=narration))
        return messages

    @staticmethod
    def _needs_narration_at(events: Sequence[JournalEvent]) -> bool:
        return any(
            isinstance(event, CombatEnded) or (isinstance(event, RoundStarted) and event.round > 1)
            for event in events
        )
