"""Инструменты ведущего-ИИ (архитектура, раздел 5.3).

ИИ меняет игру только через эти инструменты. Каждый вызов проверяется: есть ли такой
навык, сцена, улика; разрешает ли это уровень импровизации. Проверки и урон считает
движок правил, а ИИ получает готовый исход и описывает его.

Ошибочный вызов не ломает ход: модель получает ответ с ``error`` и может исправиться.
Описания инструментов — на английском: модели следуют им надёжнее (раздел 18.3).
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_dnd.ai.schemas import ToolCall, ToolSpec
from ai_dnd.game.actors import encounter_actors
from ai_dnd.game.content import GameContent
from ai_dnd.game.events import (
    ActorsJoined,
    Attitude,
    FactNoted,
    ItemGranted,
    JournalEvent,
    NpcAttitudeChanged,
    Revealed,
    RevealedKind,
    SceneChanged,
)
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.i18n import localize
from ai_dnd.rules.actions import (
    Action,
    CheckAction,
    DamageAction,
    HealAction,
    ResourceAction,
    SaveAction,
    StartCombatAction,
)
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.events import CheckResolved
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.ruleset import Difficulty
from ai_dnd.rules.state import HIT_POINTS
from ai_dnd.scenarios.schemas import Scene, SceneCheck

MAX_FACT_LENGTH = 300


class ToolCallError(Exception):
    """Вызов нельзя выполнить. Модель получит код и подсказку и сможет исправиться."""

    def __init__(self, code: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    events: list[JournalEvent] = field(default_factory=list)
    # Что увидит модель в ответ на вызов.
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return "error" in self.result


# --- Аргументы инструментов ---


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestCheckArgs(_Args):
    actor_id: str = Field(description="Id of the character who attempts the action.")
    scene_check: str | None = Field(
        default=None,
        description="Id of a prepared check of the current scene when one fits. "
        "It has the author's exact DC and may reveal clues. Then skill and difficulty "
        "are not needed.",
    )
    skill: str | None = Field(
        default=None, description="Check id from the rules, e.g. stealth or perception."
    )
    difficulty: Difficulty | None = Field(
        default=None, description="How hard the task is. Required without scene_check."
    )


class RequestSaveArgs(_Args):
    actor_id: str = Field(description="Id of the character who makes the saving throw.")
    scene_check: str | None = Field(
        default=None, description="Id of a prepared saving throw of the current scene."
    )
    save: str | None = Field(default=None, description="Saving throw id from the rules, e.g. dex.")
    difficulty: Difficulty | None = Field(default=None, description="How hard it is to resist.")


class StartCombatArgs(_Args):
    encounter_id: str = Field(description="Id of an encounter of the current scene.")


class ChangeSceneArgs(_Args):
    scene_id: str = Field(description="Id of the scene the party moves to (see Exits).")


class RevealArgs(_Args):
    entity_id: str = Field(
        description="Id of a clue, handout, item or lore entry the players discover."
    )


class SetNpcAttitudeArgs(_Args):
    npc_id: str
    attitude: Attitude


class GrantItemArgs(_Args):
    actor_id: str = Field(description="Id of the character who gets the item.")
    item_id: str | None = Field(default=None, description="Id of a scenario item.")
    name: str | None = Field(
        default=None, description="Name of an improvised minor item, when there is no item_id."
    )


class AdjustResourceArgs(_Args):
    actor_id: str
    resource: str = Field(description="Resource id, e.g. hp, hit_dice, spell_slots_1.")
    delta: int = Field(description="Negative to spend or deal damage, positive to restore.")
    reason: str = Field(description="Short reason, e.g. 'falling rocks'.")


class NoteFactArgs(_Args):
    text: str = Field(
        description="An important fact to remember: a promise, a deal, a name, a decision."
    )


# --- Инструменты ---


@dataclass(frozen=True, slots=True)
class _Tool:
    name: str
    description: str
    args: type[_Args]
    handler: Callable[["DmTools", SessionState, Any], ToolOutcome]


class DmTools:
    """Инструменты ведущего для одной сессии."""

    def __init__(
        self, content: GameContent, setup: SessionSetup, engine: RulesEngine, rng: Rng
    ) -> None:
        self._content = content
        self._setup = setup
        self._engine = engine
        self._rng = rng
        self._tools = {tool.name: tool for tool in _TOOLS}

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(ToolSpec.from_model(tool.name, tool.description, tool.args) for tool in _TOOLS)

    def execute(self, state: SessionState, call: ToolCall) -> ToolOutcome:
        tool = self._tools.get(call.name)
        if tool is None:
            return _error("unknown_tool", allowed=sorted(self._tools))
        if call.error is not None:
            return _error("arguments_not_json", detail=call.error)
        try:
            args = tool.args.model_validate(call.arguments)
        except ValidationError as error:
            problems = [
                f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
                for issue in error.errors(include_url=False, include_input=False)
            ]
            return _error("invalid_arguments", problems=problems[:10])
        try:
            return tool.handler(self, state, args)
        except ToolCallError as problem:
            return _error(problem.code, **problem.details)
        except RulesError as error:
            return _error(error.code, **{k: str(v) for k, v in error.params.items()})

    # --- Проверки ---

    def request_check(self, state: SessionState, args: RequestCheckArgs) -> ToolOutcome:
        return self._check(
            state, args.actor_id, args.scene_check, args.skill, args.difficulty, "check"
        )

    def request_save(self, state: SessionState, args: RequestSaveArgs) -> ToolOutcome:
        return self._check(
            state, args.actor_id, args.scene_check, args.save, args.difficulty, "save"
        )

    def _check(
        self,
        state: SessionState,
        actor_id: str,
        scene_check_id: str | None,
        subject: str | None,
        difficulty: Difficulty | None,
        kind: Literal["check", "save"],
    ) -> ToolOutcome:
        actor = self._actor(state, actor_id)
        prepared: SceneCheck | None = None
        target: int | None = None
        if scene_check_id is not None:
            prepared = self._scene_check(state, scene_check_id)
            subject = prepared.skill if kind == "check" else prepared.save
            if subject is None:
                other = "request_save" if kind == "check" else "request_check"
                raise ToolCallError("wrong_tool_for_scene_check", use=other)
            target = prepared.dc
            difficulty = prepared.difficulty or difficulty

        known = self._content.ruleset.checks if kind == "check" else self._content.ruleset.saves
        if subject is None or subject not in known:
            raise ToolCallError(f"unknown_{kind}", allowed=sorted(known))
        if target is None and difficulty is None:
            raise ToolCallError(
                "difficulty_required", allowed=[level.value for level in Difficulty]
            )

        action: Action
        if kind == "check":
            action = CheckAction(
                actor_id=actor, check=subject, difficulty=difficulty, target=target
            )
        else:
            action = SaveAction(actor_id=actor, save=subject, difficulty=difficulty, target=target)
        events: list[JournalEvent] = list(self._engine.resolve(state.rules, action, self._rng))
        resolved = next(event for event in events if isinstance(event, CheckResolved))

        result: dict[str, Any] = {
            "actor": state.rules.actor(actor).name,
            kind: subject,
            "outcome": resolved.outcome.value,
            "succeeded": resolved.outcome.succeeded,
            "total": resolved.total,
            "dc": resolved.target,
        }
        if resolved.auto_failed_by is not None:
            result["auto_failed_by"] = resolved.auto_failed_by
        if prepared is not None:
            consequence = prepared.on_success if resolved.outcome.succeeded else prepared.on_failure
            if consequence is not None:
                if consequence.text:
                    result["author_hint"] = consequence.text
                revealed = []
                for entity_id in consequence.reveal:
                    kind_of = self._entity_kind(entity_id)
                    if kind_of is not None and not state.is_revealed(entity_id):
                        events.append(Revealed(entity_id=entity_id, kind=kind_of))
                        revealed.append({"id": entity_id, "text": self._entity_text(entity_id)})
                if revealed:
                    result["revealed"] = revealed
        result["instruction"] = "Narrate this outcome as is. Do not change, soften or reroll it."
        return ToolOutcome(events=events, result=result)

    # --- Бой ---

    def start_combat(self, state: SessionState, args: StartCombatArgs) -> ToolOutcome:
        scenario = self._content.scenario
        encounter = next(
            (item for item in scenario.encounters if item.id == args.encounter_id), None
        )
        if encounter is None:
            raise ToolCallError("unknown_encounter", allowed=list(self._scene(state).encounters))
        if (
            self._setup.improvisation != "free"
            and encounter.id not in self._scene(state).encounters
        ):
            raise ToolCallError(
                "encounter_not_in_scene", allowed=list(self._scene(state).encounters)
            )
        if state.rules.combat is not None:
            raise ToolCallError("combat_already_started")

        creatures = encounter_actors(
            scenario, encounter.id, self._content.bestiaries, taken=state.rules.actors
        )
        joined = ActorsJoined(actors=tuple(creatures))
        after_join = apply_events(state, [joined])
        party = tuple(
            actor_id for actor_id in self._setup.party if not after_join.rules.actors[actor_id].dead
        )
        action = StartCombatAction(
            sides={"party": party, "enemies": tuple(creature.id for creature in creatures)}
        )
        events: list[JournalEvent] = [
            joined,
            *self._engine.resolve(after_join.rules, action, self._rng),
        ]
        return ToolOutcome(
            events=events,
            result={
                "combat": "started",
                "enemies": [creature.name for creature in creatures],
                "instruction": (
                    "Describe in two or three sentences how the fight begins. Do not resolve "
                    "any attacks or damage: the engine runs the fight turn by turn."
                ),
            },
        )

    # --- Мир ---

    def change_scene(self, state: SessionState, args: ChangeSceneArgs) -> ToolOutcome:
        scenario = self._content.scenario
        target = next((scene for scene in scenario.scenes if scene.id == args.scene_id), None)
        current = self._scene(state)
        if target is None:
            raise ToolCallError("unknown_scene", allowed=list(current.exits))
        if target.id == current.id:
            raise ToolCallError("already_in_scene")
        if self._setup.improvisation != "free" and target.id not in current.exits:
            raise ToolCallError("scene_not_reachable", allowed=list(current.exits))
        if state.rules.combat is not None:
            raise ToolCallError("combat_in_progress")
        result: dict[str, Any] = {"scene": target.title}
        if target.read_aloud:
            result["read_aloud"] = target.read_aloud
        result["instruction"] = "Describe the arrival using the read-aloud text as a basis."
        return ToolOutcome(
            events=[SceneChanged(scene_id=target.id, previous=current.id)], result=result
        )

    def reveal(self, state: SessionState, args: RevealArgs) -> ToolOutcome:
        kind = self._entity_kind(args.entity_id)
        if kind is None:
            raise ToolCallError("unknown_entity")
        if state.is_revealed(args.entity_id):
            return ToolOutcome(result={"already_revealed": True})
        if self._setup.improvisation == "strict" and not self._in_scene(state, args.entity_id):
            raise ToolCallError("entity_not_in_scene")
        return ToolOutcome(
            events=[Revealed(entity_id=args.entity_id, kind=kind)],
            result={"revealed": {"id": args.entity_id, "text": self._entity_text(args.entity_id)}},
        )

    def set_npc_attitude(self, state: SessionState, args: SetNpcAttitudeArgs) -> ToolOutcome:
        npc_ids = [npc.id for npc in self._content.scenario.npcs]
        if args.npc_id not in npc_ids:
            raise ToolCallError("unknown_npc", allowed=npc_ids)
        if self._setup.improvisation == "strict" and args.npc_id not in self._scene(state).npcs:
            raise ToolCallError("npc_not_in_scene", allowed=list(self._scene(state).npcs))
        return ToolOutcome(
            events=[NpcAttitudeChanged(npc_id=args.npc_id, attitude=args.attitude)],
            result={"npc": args.npc_id, "attitude": args.attitude},
        )

    def grant_item(self, state: SessionState, args: GrantItemArgs) -> ToolOutcome:
        actor = self._actor(state, args.actor_id, party_only=True)
        improvisation = self._setup.improvisation
        events: list[JournalEvent] = []
        if args.item_id is not None:
            item = next(
                (item for item in self._content.scenario.items if item.id == args.item_id), None
            )
            if item is None:
                raise ToolCallError("unknown_item")
            if improvisation == "strict" and item.id not in self._scene(state).items:
                raise ToolCallError("item_not_in_scene", allowed=list(self._scene(state).items))
            if not state.is_revealed(item.id):
                events.append(Revealed(entity_id=item.id, kind="item"))
            events.append(ItemGranted(actor_id=actor, item_id=item.id, name=item.name))
            name = item.name
        elif args.name:
            if improvisation == "strict":
                raise ToolCallError("improvised_items_not_allowed")
            name = args.name.strip()[:80]
            events.append(ItemGranted(actor_id=actor, item_id=None, name=name))
        else:
            raise ToolCallError("item_id_or_name_required")
        return ToolOutcome(events=events, result={"granted": name, "to": actor})

    def adjust_resource(self, state: SessionState, args: AdjustResourceArgs) -> ToolOutcome:
        actor_id = self._actor(state, args.actor_id)
        actor = state.rules.actor(actor_id)
        if args.resource not in actor.resources:
            raise ToolCallError("unknown_resource", allowed=sorted(actor.resources))
        if args.delta == 0:
            raise ToolCallError("delta_is_zero")
        action: Action
        if args.resource == HIT_POINTS:
            amount = str(abs(args.delta))
            action = (
                DamageAction(target_id=actor_id, damage=amount)
                if args.delta < 0
                else HealAction(target_id=actor_id, amount=amount)
            )
        else:
            action = ResourceAction(actor_id=actor_id, resource=args.resource, delta=args.delta)
        events, after = self._engine.run(state.rules, action, self._rng)
        resource = after.actor(actor_id).resources[args.resource]
        return ToolOutcome(
            events=list(events),
            result={"resource": args.resource, "current": resource.current, "max": resource.max},
        )

    def note_fact(self, state: SessionState, args: NoteFactArgs) -> ToolOutcome:
        text = args.text.strip()
        if not text:
            raise ToolCallError("empty_fact")
        return ToolOutcome(events=[FactNoted(text=text[:MAX_FACT_LENGTH])], result={"noted": True})

    # --- Внутреннее ---

    def _actor(self, state: SessionState, actor_id: str, *, party_only: bool = False) -> str:
        party = self._setup.party
        if party_only and actor_id not in party:
            raise ToolCallError("not_a_party_member", allowed=list(party))
        if actor_id not in state.rules.actors:
            raise ToolCallError("unknown_actor", allowed=list(party))
        if state.rules.actors[actor_id].dead:
            raise ToolCallError("actor_dead")
        return actor_id

    def _scene(self, state: SessionState) -> Scene:
        return next(scene for scene in self._content.scenario.scenes if scene.id == state.scene_id)

    def _scene_check(self, state: SessionState, check_id: str) -> SceneCheck:
        scene = self._scene(state)
        check = next((item for item in scene.checks if item.id == check_id), None)
        if check is None:
            raise ToolCallError("unknown_scene_check", allowed=[item.id for item in scene.checks])
        return check

    def _in_scene(self, state: SessionState, entity_id: str) -> bool:
        scene = self._scene(state)
        return entity_id in (*scene.clues, *scene.handouts, *scene.items)

    def _entity_kind(self, entity_id: str) -> RevealedKind | None:
        scenario = self._content.scenario
        groups: Sequence[tuple[RevealedKind, Sequence[str]]] = (
            ("clue", [item.id for item in scenario.clues]),
            ("handout", [item.id for item in scenario.handouts]),
            ("item", [item.id for item in scenario.items]),
            ("lore", [item.id for item in scenario.lore]),
        )
        return next((kind for kind, ids in groups if entity_id in ids), None)

    def _entity_text(self, entity_id: str) -> str:
        scenario = self._content.scenario
        for clue in scenario.clues:
            if clue.id == entity_id:
                return clue.text
        for handout in scenario.handouts:
            if handout.id == entity_id:
                return f"{handout.title}: {handout.text}"
        for item in scenario.items:
            if item.id == entity_id:
                return f"{item.name}: {item.description or ''}".rstrip(": ")
        for lore in scenario.lore:
            if lore.id == entity_id:
                return f"{lore.title}: {lore.text}"
        return ""


def _error(code: str, **details: object) -> ToolOutcome:
    return ToolOutcome(result={"error": code, **details})


def tool_result_text(outcome: ToolOutcome) -> str:
    """Ответ инструмента для модели: компактный JSON с текстами на языке игры."""
    return json.dumps(outcome.result, ensure_ascii=False)


def localized_name(text: object, language: str) -> str:
    return localize(text, language) if isinstance(text, (str, dict)) else str(text)


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "request_check",
        "Ask the rules engine to roll an ability or skill check when an action is risky, "
        "contested or uncertain. The engine returns the outcome; narrate it as is.",
        RequestCheckArgs,
        DmTools.request_check,
    ),
    _Tool(
        "request_save",
        "Ask the rules engine to roll a saving throw against a danger.",
        RequestSaveArgs,
        DmTools.request_save,
    ),
    _Tool(
        "start_combat",
        "Start a fight with an encounter of the current scene. The engine then runs the "
        "fight turn by turn; do not narrate attacks or damage yourself.",
        StartCombatArgs,
        DmTools.start_combat,
    ),
    _Tool(
        "change_scene",
        "Move the party to another scene when they decide to go there.",
        ChangeSceneArgs,
        DmTools.change_scene,
    ),
    _Tool(
        "reveal",
        "Mark a clue, handout, item or lore entry as discovered by the players.",
        RevealArgs,
        DmTools.reveal,
    ),
    _Tool(
        "set_npc_attitude",
        "Record that an NPC's attitude towards the party changed.",
        SetNpcAttitudeArgs,
        DmTools.set_npc_attitude,
    ),
    _Tool(
        "grant_item",
        "Give an item to a character: a scenario item by id or, if allowed, a minor "
        "improvised item by name.",
        GrantItemArgs,
        DmTools.grant_item,
    ),
    _Tool(
        "adjust_resource",
        "Change a character's resource outside combat: damage from a trap or fall, "
        "healing, a spent spell slot.",
        AdjustResourceArgs,
        DmTools.adjust_resource,
    ),
    _Tool(
        "note_fact",
        "Remember an important fact for later: a promise, a deal, a name, a decision.",
        NoteFactArgs,
        DmTools.note_fact,
    ),
)
