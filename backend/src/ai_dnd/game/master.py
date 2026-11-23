"""Ведущий-ИИ: private decisions, engine effects, then public narration."""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.provider import LLMError
from ai_dnd.ai.recorder import CallContext
from ai_dnd.ai.schemas import LLMResponse, Message, ToolCall
from ai_dnd.game.content import GameContent
from ai_dnd.game.context import ActionLine, ContextBuilder
from ai_dnd.game.decision import ActionDecision, TurnDecision
from ai_dnd.game.events import JournalEvent, Narrated, PartyDefeated, PlayerActed, SummaryUpdated
from ai_dnd.game.journal import Journal
from ai_dnd.game.memory import SUMMARY_EVERY, build_summary
from ai_dnd.game.render import ModelLogRenderer
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.game.spells import CURE_WOUNDS_TOOL
from ai_dnd.game.tools import (
    LOOKUP_LORE_SPEC,
    LOOKUP_LORE_TOOL,
    AdjustResourceArgs,
    CastCureWoundsArgs,
    DmTools,
    EnhanceItemArgs,
    EquipItemArgs,
    LookupLoreArgs,
    ProtectArgs,
    StealthArgs,
    ToolOutcome,
    UnequipItemArgs,
    UseItemArgs,
    item_mechanics,
)
from ai_dnd.rag.search import LoreSearcher, SearchHit, format_fragments
from ai_dnd.rules.actions import Action
from ai_dnd.rules.combat import party_wiped
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.events import (
    ActorDied,
    ActorFled,
    AttackResolved,
    CombatEnded,
    ConditionApplied,
    DamageDealt,
    Healed,
    RoundStarted,
)
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.ruleset import Outcome
from ai_dnd.rules.state import CombatState
from ai_dnd.rules.tactics import choose_action

MAX_PLANNING_CALLS = 4  # move, follow-up tool, engage, and at most one repair
MAX_REPAIRS = 1
EFFECT_TOOLS = frozenset(
    {
        "request_check",
        "request_save",
        "reveal",
        "set_npc_attitude",
        "move_npc",
        "kill_npc",
        "grant_item",
        "equip_item",
        "unequip_item",
        "use_item",
        "enhance_item",
        "adjust_resource",
        CURE_WOUNDS_TOOL,
        "note_fact",
        "protect",
        "stealth",
    }
)
# Сколько прошлых ходов идёт в промпт дословно (N). Более старые сворачиваются в сводку
# раз в SUMMARY_EVERY ходов (раздел 9.3, подэтап 2.4).
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


@dataclass(slots=True)
class _PendingOperation:
    refs: tuple[int, ...]
    name: str
    arguments: dict[str, Any]
    outcome: ToolOutcome
    keep_refs: frozenset[int] = frozenset()


class _SceneNotReachableError(ValueError):
    """Отдельный тип: недостижимая сцена чинится repair-ом, а при исчерпании бюджета —
    отказом в мире, но никогда системной ошибкой хода."""


@dataclass(slots=True)
class _PendingTurn:
    inputs: tuple[PlayerInput, ...]
    actions: tuple[ActionLine, ...]
    unresolved: set[int]
    completed: list[dict[str, Any]] = field(default_factory=list)
    input_committed: bool = False
    planning_calls: int = 0
    repairs: int = 0
    problem: str = ""
    moved: bool = False
    unreachable: bool = False
    pending_operation: _PendingOperation | None = None
    narration_text: str | None = None


class GameMaster:
    def __init__(
        self,
        content: GameContent,
        setup: SessionSetup,
        state: SessionState,
        gateway: LLMGateway,
        journal: Journal,
        rng: Rng,
        searcher: LoreSearcher | None = None,
    ) -> None:
        self._content = content
        self._setup = setup
        self._state = state
        self._gateway = gateway
        self._journal = journal
        self._rng = rng
        self._searcher = searcher
        self._engine = RulesEngine(content.ruleset, item_mechanics(content.scenario))
        self._tools = DmTools(content, setup, self._engine, rng)
        self._context = ContextBuilder(content, setup)
        self._query_tools = frozenset({LOOKUP_LORE_TOOL}) if searcher is not None else frozenset()
        specs = [spec for spec in self._tools.specs() if spec.name in EFFECT_TOOLS]
        if searcher is not None:
            specs.append(LOOKUP_LORE_SPEC)
        effect_specs = [spec.model_dump(mode="json") for spec in specs]
        self._decision_system_prompt = (
            self._context.decision_prompt
            + "\n\n# Actual effect tool contracts\n"
            + json.dumps(effect_specs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        if searcher is not None:
            self._decision_system_prompt += (
                "\n\n# Query tool (not a world effect)\n"
                f"`{LOOKUP_LORE_TOOL}` searches the scenario for knowledge the current scene lacks "
                "(a place, faction, person or object the players ask about). Set tool_name and "
                'arguments={"query":"..."} with continue_after=true; it returns fragments and '
                "changes nothing. Use it at most once per input when background knowledge is "
                "genuinely needed, then decide that input from the returned fragments. Do not "
                "reveal DM-only fragments to players."
            )
        self._log_renderer = ModelLogRenderer(content)
        # Прошлые ходы: (как ход выглядит в истории, рассказ ведущего).
        self._history: list[tuple[str, str]] = []
        # События боя, которые модель ещё не описала.
        self._combat_log: list[JournalEvent] = []
        # Последний состав боя нужен после CombatEnded: в правилах он уже очищен.
        self._last_combat: CombatState | None = state.rules.combat
        self._call_context = CallContext(session_id=journal.session_id)
        self._pending_turn: _PendingTurn | None = None
        self._pending_combat_text: str | None = None
        self._combat_narration_pending = False

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
        if self._pending_turn is not None:
            raise LLMError("pending_turn", "Resolve the previous turn first")
        messages = [
            Message.system(self._context.system_prompt),
            Message.user(self._context.turn_message(self._state, (), opening=True)),
        ]
        narration, response = await self._narration_response(messages)
        yield ModelCalled(response)
        await self._commit([Narrated(text=narration)])
        self._history.append((self._context.history_entry(()), narration))
        yield NarrationChunk(narration)

    async def play_turn(self, inputs: Sequence[PlayerInput]) -> AsyncIterator[TurnUpdate]:
        """Resolve a fresh turn. A pending failure must be retried explicitly."""
        if self._pending_turn is not None:
            raise LLMError("pending_turn", "Retry the unresolved turn before new input")
        if self.in_combat or self._combat_narration_pending:
            raise LLMError("combat_in_progress", "Use combat actions")
        if not inputs:
            raise ValueError("A turn needs at least one player input")
        actions = tuple(
            ActionLine(
                actor_id=item.actor_id,
                name=self._state.rules.actor(item.actor_id).name,
                text=item.text,
            )
            for item in inputs
        )
        self._pending_turn = _PendingTurn(tuple(inputs), actions, set(range(len(inputs))))
        async for update in self.retry_turn():
            yield update

    @property
    def has_pending_turn(self) -> bool:
        return self._pending_turn is not None

    @property
    def has_pending_combat_narration(self) -> bool:
        return self._combat_narration_pending

    async def retry_turn(self) -> AsyncIterator[TurnUpdate]:
        """Resume only unresolved operations or narration; never repeat committed effects."""
        pending = self._pending_turn
        if pending is None:
            raise LLMError("no_pending_turn")
        if pending.input_committed:
            # An explicit caller retry starts a new bounded planning attempt.
            pending.planning_calls = 0
            pending.repairs = 0
        if not pending.input_committed:
            acted: list[JournalEvent] = [
                PlayerActed(actor_id=item.actor_id, text=item.text) for item in pending.inputs
            ]
            await self._commit(acted)
            pending.input_committed = True
            yield EventsHappened(tuple(acted))

        if pending.pending_operation is not None:
            async for update in self._commit_operation(pending, pending.pending_operation):
                yield update

        # Предварительный поиск по тексту действий — один раз на ход. Планировщик видит
        # public+dm фрагменты; публичный рассказ — только public (видимость при передаче).
        # Системный промпт и история не меняются, кэш начала сохраняется.
        hits = await self._search_hits(pending.actions)
        fragments = format_fragments(hits)
        public_fragments = format_fragments([hit for hit in hits if hit.visibility == "public"])
        while pending.unresolved:
            if pending.planning_calls >= MAX_PLANNING_CALLS:
                if not pending.unreachable:
                    raise LLMError("invalid_turn_decision", "Planning call limit reached")
                self._refuse_travel(pending)
                break
            pending.planning_calls += 1
            refs = sorted(pending.unresolved)
            message = self._context.decision_message(
                self._state,
                [(index, pending.actions[index]) for index in refs],
                pending.completed,
                moved=pending.moved,
                problem=pending.problem,
                fragments=fragments,
            )
            response = await self._gateway.complete(
                "dm_tools",
                [
                    Message.system(self._decision_system_prompt),
                    *self._history_messages(),
                    Message.user(message),
                ],
                json_output=True,
                context=self._call_context,
            )
            yield ModelCalled(response)
            try:
                decision = TurnDecision.model_validate_json(response.text)
                scene_moves = self._validate_decision(pending, decision)
            except ValidationError as error:
                problem = "; ".join(
                    f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                    for issue in error.errors(include_url=False, include_input=False)[:8]
                )
                self._repair(pending, f"invalid_decision: {problem}")
                continue
            except _SceneNotReachableError as error:
                pending.unreachable = True
                self._repair_or_refuse(pending, f"invalid_decision: {error}")
                continue
            except ValueError as error:
                self._repair(pending, f"invalid_decision: {error}")
                continue
            pending.problem = ""
            plans = decision.actions
            engages = [plan for plan in plans if plan.kind == "engage"]
            if scene_moves:
                first = scene_moves[0]
                operation = self._resolve_operation(
                    tuple(plan.input_index for plan in scene_moves),
                    "change_scene",
                    {"scene_id": first.scene_id},
                    keep_refs=frozenset(
                        plan.input_index for plan in scene_moves if plan.continue_after
                    ),
                )
                if operation.outcome.failed:
                    if operation.outcome.result.get("error") == "scene_not_reachable":
                        pending.unreachable = True
                        self._repair_or_refuse(pending, self._operation_error(operation))
                    else:
                        self._repair(pending, self._operation_error(operation))
                    continue
                async for update in self._commit_operation(pending, operation):
                    yield update
                # Every other original action is reconsidered against the fresh scene.
                continue
            if engages:
                first = engages[0]
                operation = self._resolve_operation(
                    tuple(plan.input_index for plan in engages),
                    "start_combat",
                    {"encounter_id": first.encounter_id},
                )
                if operation.outcome.failed:
                    self._repair(pending, self._operation_error(operation))
                    continue
                async for update in self._commit_operation(pending, operation):
                    yield update
                for plan in plans:
                    if plan.kind == "stay":
                        self._complete_stay(pending, plan.input_index)
                continue

            failed = False
            for plan in plans:
                if plan.kind == "stay":
                    self._complete_stay(pending, plan.input_index)
                    continue
                if plan.kind == "defer":
                    continue
                if plan.kind == "move":
                    self._complete_local_move(pending, plan)
                    continue
                assert plan.kind == "tool"
                assert plan.tool_name is not None
                if plan.tool_name == LOOKUP_LORE_TOOL:
                    async for update in self._commit_lookup(pending, plan):
                        yield update
                    continue
                operation = self._resolve_operation(
                    (plan.input_index,),
                    plan.tool_name,
                    plan.arguments,
                    keep_refs=(
                        frozenset({plan.input_index}) if plan.continue_after else frozenset()
                    ),
                )
                if operation.outcome.failed:
                    self._repair(pending, self._operation_error(operation))
                    failed = True
                    break
                if (
                    plan.continue_after
                    and not operation.outcome.events
                    and any(item.kind == "defer" for item in plans)
                ):
                    self._repair(pending, "deferred inputs require a committed tool outcome")
                    failed = True
                    break
                async for update in self._commit_operation(pending, operation):
                    yield update
            if failed:
                continue

        if pending.narration_text is None:
            messages = [
                Message.system(self._context.system_prompt),
                *self._history_messages(),
                Message.user(
                    self._context.narration_message(
                        self._state, pending.actions, pending.completed, fragments=public_fragments
                    )
                ),
            ]
            narration, response = await self._narration_response(messages)
            pending.narration_text = narration
            yield ModelCalled(response)
        narration = pending.narration_text
        await self._commit([Narrated(text=narration)])
        self._history.append((self._context.history_entry(pending.actions), narration))
        self._pending_turn = None
        yield NarrationChunk(narration)
        # Сводка старых ходов — после рассказа игрокам, отдельным вызовом задачи summary.
        await self._maybe_summarize()

    def _validate_decision(
        self, pending: _PendingTurn, decision: TurnDecision
    ) -> list[ActionDecision]:
        refs = [plan.input_index for plan in decision.actions]
        if len(refs) != len(set(refs)) or set(refs) != pending.unresolved:
            raise ValueError(f"input coverage must equal {sorted(pending.unresolved)}")
        scene_id = self._state.scene_id
        scene_moves: list[ActionDecision] = []
        has_local_moves = False
        for plan in decision.actions:
            if plan.actor_id != pending.inputs[plan.input_index].actor_id:
                raise ValueError(f"actor mismatch at input {plan.input_index}")
            if plan.kind == "move":
                if not plan.scene_id:
                    raise ValueError("scene_id missing")
                if plan.scene_id == scene_id:
                    if any(
                        item.get("kind") == "local_move"
                        and item.get("scene_id") == scene_id
                        and plan.input_index in item["input_indices"]
                        for item in pending.completed
                    ):
                        raise ValueError("completed local move may not be repeated for this input")
                    has_local_moves = True
                else:
                    if pending.moved:
                        raise ValueError("scene change already resolved in this turn")
                    exits = self._scene_exits()
                    if self._setup.improvisation != "free" and plan.scene_id not in exits:
                        raise _SceneNotReachableError(
                            f"scene_not_reachable: '{plan.scene_id}' is not an exit of "
                            f"'{scene_id}'. Allowed exits: {', '.join(exits)}. "
                            "Resolve this input as stay or local_move and explain the "
                            "refusal in-world; do not name unreachable locations."
                        )
                    scene_moves.append(plan)
            elif plan.scene_id is not None:
                raise ValueError("scene_id only applies to move")
            if plan.kind == "engage":
                if not plan.encounter_id:
                    raise ValueError("encounter_id missing")
            elif plan.encounter_id is not None:
                raise ValueError("encounter_id only applies to engage")
            if plan.kind == "tool":
                if plan.tool_name not in EFFECT_TOOLS and plan.tool_name not in self._query_tools:
                    raise ValueError("tool is not an allowed effect")
                if plan.tool_name == LOOKUP_LORE_TOOL:
                    if not plan.continue_after:
                        raise ValueError("lookup_lore requires continue_after=true")
                    LookupLoreArgs.model_validate(plan.arguments)
                if (
                    plan.tool_name in ("request_check", "request_save")
                    and plan.arguments.get("actor_id") != plan.actor_id
                ):
                    raise ValueError("check actor must match input actor")
                if plan.tool_name == CURE_WOUNDS_TOOL:
                    args = CastCureWoundsArgs.model_validate(plan.arguments)
                    if args.caster_id != plan.actor_id:
                        raise ValueError("Cure Wounds caster must match input actor")
                if plan.tool_name == "protect":
                    protection = ProtectArgs.model_validate(plan.arguments)
                    if protection.protector_id != plan.actor_id:
                        raise ValueError("protect actor must match input actor")
                if plan.tool_name == "stealth":
                    hiding = StealthArgs.model_validate(plan.arguments)
                    if hiding.actor_id != plan.actor_id:
                        raise ValueError("stealth actor must match input actor")
                if plan.tool_name == "equip_item":
                    equipping = EquipItemArgs.model_validate(plan.arguments)
                    if equipping.actor_id != plan.actor_id:
                        raise ValueError("equip actor must match input actor")
                if plan.tool_name == "unequip_item":
                    unequipping = UnequipItemArgs.model_validate(plan.arguments)
                    if unequipping.actor_id != plan.actor_id:
                        raise ValueError("unequip actor must match input actor")
                if plan.tool_name == "use_item":
                    using = UseItemArgs.model_validate(plan.arguments)
                    if using.actor_id != plan.actor_id:
                        raise ValueError("use_item actor must match input actor")
                if plan.tool_name == "enhance_item":
                    enhancing = EnhanceItemArgs.model_validate(plan.arguments)
                    if enhancing.actor_id != plan.actor_id:
                        raise ValueError("enhance actor must match input actor")
                if plan.tool_name == "adjust_resource":
                    adjustment = AdjustResourceArgs.model_validate(plan.arguments)
                    if (restriction := adjustment.restriction()) is not None:
                        raise ValueError(restriction)
                if self._is_repeated_effect(pending, plan):
                    raise ValueError("completed effect may not be repeated for this input")
            elif plan.tool_name is not None or plan.arguments:
                raise ValueError("tool fields only apply to tool action")
            if plan.continue_after and plan.kind not in ("move", "tool"):
                raise ValueError("continue_after applies only to move or tool")
        engages = [plan for plan in decision.actions if plan.kind == "engage"]
        if scene_moves and has_local_moves:
            raise ValueError("local moves and scene changes need separate decisions")
        if scene_moves and engages:
            raise ValueError("move and engage need separate scene phases")
        if scene_moves:
            if len({plan.scene_id for plan in scene_moves}) != 1:
                raise ValueError("collective moves must share destination")
            if any(plan.kind == "tool" for plan in decision.actions):
                raise ValueError("effects after movement need a fresh-scene decision")
        if engages and (
            len({plan.encounter_id for plan in engages}) != 1
            or any(plan.kind not in ("engage", "stay") for plan in decision.actions)
        ):
            raise ValueError("engage decision may only include matching engage or stay")
        if any(plan.kind == "defer" for plan in decision.actions):
            if not (
                scene_moves or has_local_moves or any(p.kind == "tool" for p in decision.actions)
            ):
                raise ValueError("defer requires a move or tool in the same decision")
            if has_local_moves and all(
                plan.kind == "defer" or (plan.kind == "move" and plan.continue_after)
                for plan in decision.actions
            ):
                raise ValueError("deferred inputs require progress beyond a continuing local move")
        return scene_moves

    def _is_repeated_effect(self, pending: _PendingTurn, plan: ActionDecision) -> bool:
        """Block a repeated effect for one source action in one scene."""
        proposed_subject = self._check_subject(plan)
        for item in pending.completed:
            if (
                plan.input_index not in item.get("input_indices", [])
                or item.get("tool") != plan.tool_name
                or item.get("scene_id_after") != self._state.scene_id
            ):
                continue
            if plan.tool_name == CURE_WOUNDS_TOOL:
                return True
            previous_args = item.get("arguments", {})
            if plan.tool_name in ("request_check", "request_save"):
                result = item.get("result", {})
                subject_key = "check" if plan.tool_name == "request_check" else "save"
                if proposed_subject is not None and proposed_subject == result.get(subject_key):
                    return True
            elif plan.tool_name == "adjust_resource":
                if previous_args.get("actor_id") == plan.arguments.get(
                    "actor_id"
                ) and previous_args.get("resource") == plan.arguments.get("resource"):
                    return True
            elif plan.tool_name == "grant_item":
                if previous_args.get("item_id") == plan.arguments.get(
                    "item_id"
                ) and previous_args.get("name") == plan.arguments.get("name"):
                    return True
            elif previous_args == plan.arguments:
                return True
        return False

    def _check_subject(self, plan: ActionDecision) -> str | None:
        key = "skill" if plan.tool_name == "request_check" else "save"
        scene_check = plan.arguments.get("scene_check")
        if isinstance(scene_check, str):
            scene = next(
                scene for scene in self._content.scenario.scenes if scene.id == self._state.scene_id
            )
            prepared = next((check for check in scene.checks if check.id == scene_check), None)
            return getattr(prepared, key) if prepared is not None else None
        value = plan.arguments.get(key)
        return value if isinstance(value, str) else None

    @staticmethod
    def _repair(pending: _PendingTurn, problem: str) -> None:
        pending.repairs += 1
        pending.problem = problem[:500]
        if pending.repairs > MAX_REPAIRS:
            raise LLMError("invalid_turn_decision", "Decision validation failed")

    def _scene_exits(self) -> tuple[str, ...]:
        return next(
            scene.exits
            for scene in self._content.scenario.scenes
            if scene.id == self._state.scene_id
        )

    def _repair_or_refuse(self, pending: _PendingTurn, problem: str) -> None:
        try:
            self._repair(pending, problem)
        except LLMError:
            self._refuse_travel(pending)

    @staticmethod
    def _refuse_travel(pending: _PendingTurn) -> None:
        # Исчерпанный бюджет repair на недостижимой сцене: ход обязан завершиться
        # нарративом-отказом в мире, а не системной ошибкой.
        for ref in sorted(pending.unresolved):
            pending.completed.append(
                {
                    "input_indices": [ref],
                    "kind": "stay",
                    "events": [],
                    "result": {
                        "instruction": (
                            "The requested travel is impossible from the current scene: "
                            "the destination is not among its exits. The party stays here. "
                            "Explain the refusal in-world (no path, an obstacle, danger) "
                            "without naming unreachable locations, and point to the "
                            "reachable exits listed above."
                        )
                    },
                }
            )
        pending.unresolved.clear()
        pending.problem = ""

    def _resolve_operation(
        self,
        refs: tuple[int, ...],
        name: str,
        arguments: dict[str, Any],
        *,
        keep_refs: frozenset[int] = frozenset(),
    ) -> _PendingOperation:
        call = ToolCall(id="decision", name=name, arguments=arguments)
        return _PendingOperation(
            refs=refs,
            name=name,
            arguments=arguments,
            outcome=self._tools.execute(self._state, call),
            keep_refs=keep_refs,
        )

    @staticmethod
    def _operation_error(operation: _PendingOperation) -> str:
        return json.dumps(
            {
                "tool": operation.name,
                "result": operation.outcome.result,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )[:500]

    async def _commit_operation(
        self, pending: _PendingTurn, operation: _PendingOperation
    ) -> AsyncIterator[TurnUpdate]:
        pending.pending_operation = operation
        if operation.outcome.events:
            await self._commit(operation.outcome.events)
        pending.pending_operation = None
        pending.completed.append(
            {
                "input_indices": list(operation.refs),
                "tool": operation.name,
                "arguments": operation.arguments,
                "result": operation.outcome.result,
                "events": [event.type for event in operation.outcome.events],
                "scene_id_after": self._state.scene_id,
            }
        )
        pending.unresolved.difference_update(operation.refs)
        pending.unresolved.update(operation.keep_refs)
        if operation.name == "change_scene":
            pending.moved = True
        if operation.outcome.events:
            yield EventsHappened(tuple(operation.outcome.events))
        yield ToolUsed(operation.name, operation.arguments, operation.outcome.result)

    @staticmethod
    def _complete_stay(pending: _PendingTurn, ref: int) -> None:
        pending.completed.append({"input_indices": [ref], "kind": "stay", "events": []})
        pending.unresolved.discard(ref)

    def _complete_local_move(self, pending: _PendingTurn, plan: ActionDecision) -> None:
        scene_id = self._state.scene_id
        pending.completed.append(
            {
                "input_indices": [plan.input_index],
                "kind": "local_move",
                "actor_id": plan.actor_id,
                "scene_id": scene_id,
                "scene_id_after": scene_id,
                "events": [],
                "result": {
                    "instruction": "Describe only safe repositioning within the current scene. "
                    "Leave any obstacle or risky attempt unresolved until an engine outcome."
                },
            }
        )
        if not plan.continue_after:
            pending.unresolved.discard(plan.input_index)

    async def _commit_lookup(
        self, pending: _PendingTurn, plan: ActionDecision
    ) -> AsyncIterator[TurnUpdate]:
        """Инструмент запроса: поиск по сценарию без изменения мира, вход остаётся открытым."""
        args = LookupLoreArgs.model_validate(plan.arguments)
        hits = await self._searcher.search(args.query) if self._searcher is not None else []
        fragments = format_fragments(hits)
        result: dict[str, Any] = {"fragments": fragments or ["(no relevant fragments found)"]}
        pending.completed.append(
            {
                "input_indices": [plan.input_index],
                "tool": LOOKUP_LORE_TOOL,
                "arguments": plan.arguments,
                "result": result,
                "events": [],
                "scene_id_after": self._state.scene_id,
            }
        )
        pending.unresolved.add(plan.input_index)
        yield ToolUsed(LOOKUP_LORE_TOOL, plan.arguments, result)

    async def _narration_response(self, messages: Sequence[Message]) -> tuple[str, LLMResponse]:
        response = await self._gateway.complete("narration", messages, context=self._call_context)
        narration = response.text.strip()
        if not narration:
            raise LLMError("empty_response", "Narration was empty")
        return narration, response

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
        if self._pending_turn is not None:
            raise LLMError("pending_turn", "Finish the previous narration before combat")
        if self._combat_narration_pending:
            raise LLMError("pending_combat_narration")
        events: list[JournalEvent] = list(
            self._engine.resolve(self._state.rules, action, self._rng)
        )
        await self._commit(events)
        self._combat_log.extend(events)
        return events

    async def run_creature_turns(self) -> list[JournalEvent]:
        """Ходы существ по их тактике — пока не придёт очередь игрока или бой не кончится."""
        if self._pending_turn is not None:
            raise LLMError("pending_turn", "Finish the previous narration before combat")
        if self._combat_narration_pending:
            raise LLMError("pending_combat_narration")
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
        defeated = ended and party_wiped(
            self._content.ruleset, self._state.rules, self._setup.party
        )
        if not log:
            self._combat_log.clear()
            return
        roster = self._state.rules.combat or self._last_combat
        if roster is None:
            raise RuntimeError("Combat narration has no participant roster")
        fled = set(roster.fled)
        terminal_events: list[str] = []
        for combat_event in self._combat_log:
            if isinstance(combat_event, ActorFled):
                fled.add(combat_event.actor_id)
                terminal_events.append(f"ActorFled: {combat_event.actor_id}")
            elif isinstance(combat_event, ActorDied):
                terminal_events.append(f"ActorDied: {combat_event.actor_id}")
            elif isinstance(combat_event, CombatEnded):
                terminal_events.append(f"CombatEnded: winner={combat_event.winner}")
        mandatory, misses = self._mandatory_outcomes()
        message = self._context.combat_message(
            self._state,
            log,
            roster=roster,
            fled=fled,
            terminal_events=terminal_events,
            ended=ended,
            defeated=defeated,
            mandatory=mandatory,
            misses=misses,
        )
        messages = [
            Message.system(self._context.system_prompt),
            *self._history_messages(),
            Message.user(message),
        ]
        self._combat_narration_pending = True
        if self._pending_combat_text is None:
            narration, response = await self._narration_response(messages)
            self._pending_combat_text = narration
            yield ModelCalled(response)
        narration = self._pending_combat_text
        await self._commit([Narrated(text=narration)])
        self._history.append(("(Combat round)", narration))
        if defeated:
            # Маркер поражения после финального рассказа: фронтенд показывает выбор
            # «покинуть стол / начать сначала».
            await self._commit([PartyDefeated()])
        self._combat_log.clear()
        self._pending_combat_text = None
        self._combat_narration_pending = False
        yield NarrationChunk(narration)
        if ended:
            self._last_combat = None

    # --- Внутреннее ---

    def _mandatory_outcomes(self) -> tuple[list[str], int]:
        """Поштучные state-changing факты окна и число сжимаемых промахов."""
        events = self._combat_log
        actors = self._state.rules.actors

        def name(actor_id: str | None) -> str:
            if actor_id is None:
                return "—"
            actor = actors.get(actor_id)
            return actor.name if actor else actor_id

        def wound_epithet(amount: int, target_id: str) -> str:
            actor = actors.get(target_id)
            hp = getattr(actor, "hp", None)
            max_hp = getattr(hp, "max", 0) or 0
            if max_hp <= 0:
                return "wound"
            fraction = amount / max_hp
            if fraction <= 0.10:
                return "light wound"
            if fraction <= 0.25:
                return "serious wound"
            if fraction <= 0.50:
                return "heavy wound"
            return "devastating blow"

        death_index: dict[str, int] = {}
        for index, event in enumerate(events):
            if isinstance(event, ActorDied):
                death_index.setdefault(event.actor_id, index)

        def killed_by(index: int, event: DamageDealt) -> bool:
            died_at = death_index.get(event.target_id)
            return died_at is not None and died_at > index

        # Актёры, чья потеря сознания уже покрыта строкой урона (hp_after == 0).
        downed = {
            event.target_id
            for index, event in enumerate(events)
            if isinstance(event, DamageDealt)
            and event.hp_after == 0
            and not killed_by(index, event)
        }
        mandatory: list[str] = []
        misses = 0
        for index, event in enumerate(events):
            if isinstance(event, DamageDealt):
                if event.hp_after == 0 and killed_by(index, event):
                    consequence = "killed"
                elif event.hp_after == 0:
                    consequence = "downed unconscious"
                else:
                    consequence = "wounded"
                mandatory.append(
                    f"{name(event.source_id)} → {name(event.target_id)}: "
                    f"{wound_epithet(event.amount, event.target_id)} ({consequence})"
                )
            elif isinstance(event, ActorDied):
                mandatory.append(f"{name(event.actor_id)} DIED")
            elif isinstance(event, ConditionApplied) and event.condition == "unconscious":
                if event.actor_id not in downed:
                    mandatory.append(f"{name(event.actor_id)} fell unconscious")
                    downed.add(event.actor_id)
            elif isinstance(event, Healed):
                mandatory.append(
                    f"{name(event.target_id)} regains {event.amount} HP, HP {event.hp_after}"
                )
            elif isinstance(event, AttackResolved) and event.outcome in (
                Outcome.FAILURE,
                Outcome.CRITICAL_FAILURE,
            ):
                misses += 1
        return mandatory, misses

    async def _commit(self, events: Sequence[JournalEvent]) -> None:
        if not events:
            return
        previous_combat = self._state.rules.combat
        next_state = apply_events(self._state, events)
        await self._journal.append(events, next_state)
        self._state = next_state
        if next_state.rules.combat is not None:
            self._last_combat = next_state.rules.combat
        elif previous_combat is not None:
            self._last_combat = previous_combat

    def _history_messages(self) -> list[Message]:
        messages: list[Message] = []
        # Слой 4: сводка старых ходов. Меняется только раз в SUMMARY_EVERY ходов, между
        # обновлениями байт-стабильна; системный промпт (слои 1–3) не затрагивает.
        if self._state.summary:
            messages.append(Message.user(self._context.summary_entry(self._state.summary)))
        for entry, narration in self._history[-HISTORY_TURNS:]:
            messages.append(Message.user(entry))
            messages.append(Message(role="assistant", content=narration))
        return messages

    async def _maybe_summarize(self) -> None:
        """Свернуть ушедшие из дословного окна ходы в сводку — раз в K ходов, одним вызовом."""
        dropped = len(self._history) - HISTORY_TURNS
        covered = self._state.summary_through_turn
        if dropped - covered < SUMMARY_EVERY:
            return
        text = await build_summary(
            self._gateway,
            language=self._setup.language,
            previous_summary=self._state.summary,
            turns=self._history[covered:dropped],
        )
        await self._commit([SummaryUpdated(text=text, through_turn=dropped)])

    async def _search_hits(self, actions: Sequence[ActionLine]) -> list[SearchHit]:
        """Предварительный поиск по тексту действий; пусто без поиска или без запроса."""
        if self._searcher is None:
            return []
        query = " ".join(action.text for action in actions).strip()
        if not query:
            return []
        return await self._searcher.search(query)

    @staticmethod
    def _needs_narration_at(events: Sequence[JournalEvent]) -> bool:
        return any(
            isinstance(event, CombatEnded) or (isinstance(event, RoundStarted) and event.round > 1)
            for event in events
        )
