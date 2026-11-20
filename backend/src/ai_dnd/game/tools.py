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
    EncounterStarted,
    FactNoted,
    ItemGranted,
    JournalEvent,
    NpcAttitudeChanged,
    NpcDied,
    NpcMoved,
    Revealed,
    RevealedKind,
    SceneChanged,
)
from ai_dnd.game.presence import effective_location
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.game.spells import CURE_WOUNDS_TOOL, FIRST_LEVEL_SLOT, SPELL_SLOT_PREFIX
from ai_dnd.i18n import localize
from ai_dnd.rules.actions import (
    Action,
    CheckAction,
    DamageAction,
    HealAction,
    HideAction,
    ProtectAction,
    ResourceAction,
    SaveAction,
    StartCombatAction,
)
from ai_dnd.rules.combat import allies_of, incapacitated_by
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.events import CheckResolved, ConditionRemoved, DiceRolled, Healed
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


class MoveNpcArgs(_Args):
    npc_id: str = Field(description="Id of the NPC who moves to another scene.")
    scene_id: str = Field(description="Id of the scene the NPC moves to.")


class KillNpcArgs(_Args):
    npc_id: str = Field(description="Id of the NPC who dies.")


class GrantItemArgs(_Args):
    actor_id: str = Field(description="Id of the character who gets the item.")
    item_id: str | None = Field(default=None, description="Id of a scenario item.")
    name: str | None = Field(
        default=None, description="Name of an improvised minor item, when there is no item_id."
    )


class AdjustResourceArgs(_Args):
    actor_id: str
    resource: str = Field(description="Resource id, e.g. hp for damage or gp.")
    delta: int = Field(strict=True, description="Signed whole number. HP can only decrease.")
    reason: str = Field(description="Short reason, e.g. 'falling rocks'.")

    def restriction(self) -> str | None:
        if self.resource == HIT_POINTS and self.delta > 0:
            return "healing_requires_supported_action"
        if self.resource.startswith(SPELL_SLOT_PREFIX):
            return "spell_slots_require_supported_action"
        return None


class CastCureWoundsArgs(_Args):
    caster_id: str = Field(description="Party member casting Cure Wounds from the input.")
    target_id: str = Field(description="Living party member to heal. A target at 0 HP is valid.")


class ProtectArgs(_Args):
    protector_id: str = Field(
        description="Party member with the protect skill who covers an ally on their combat turn."
    )
    target_id: str | None = Field(
        default=None,
        description="Ally to cover. Omit (with target_name) to cover the most wounded living ally.",
    )
    target_name: str | None = Field(
        default=None, description="Ally by name when the id is unknown."
    )


class StealthArgs(_Args):
    actor_id: str = Field(description="Party member with the stealth skill who tries to hide.")


class NoteFactArgs(_Args):
    text: str = Field(
        description="An important fact to remember: a promise, a deal, a name, a decision."
    )


# Инструмент запроса (не effect): поиск по сценарию, исполняется ведущим асинхронно.
LOOKUP_LORE_TOOL = "lookup_lore"


class LookupLoreArgs(_Args):
    query: str = Field(
        description="What to look up in the scenario: a place, person, faction, object or lore "
        "topic the current scene does not cover. Use the players' words or names."
    )


LOOKUP_LORE_SPEC = ToolSpec.from_model(
    LOOKUP_LORE_TOOL,
    "Search the scenario for background knowledge outside the current scene. Returns matching "
    "fragments; it changes nothing in the world. Set continue_after=true, then decide the input.",
    LookupLoreArgs,
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
        # Политика прототипа: улика из on_success доступна только через успешную
        # заготовленную проверку, даже если её можно было бы найти иным способом.
        clue_ids = {clue.id for clue in content.scenario.clues}
        self._check_gated_clues = {
            entity_id
            for scene in content.scenario.scenes
            for check in scene.checks
            for consequence in (check.on_success,)
            if consequence is not None
            for entity_id in consequence.reveal
            if entity_id in clue_ids
        }

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
        if encounter.id in state.started_encounters:
            raise ToolCallError("encounter_already_started")

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
            EncounterStarted(encounter_id=encounter.id),
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
        if kind == "clue" and args.entity_id in self._check_gated_clues:
            checks = [
                check.id
                for check in self._scene(state).checks
                if check.on_success is not None and args.entity_id in check.on_success.reveal
            ]
            raise ToolCallError(
                "clue_requires_prepared_check",
                scene_checks=checks,
                instruction="A successful matching prepared check reveals this clue automatically.",
            )
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

    def move_npc(self, state: SessionState, args: MoveNpcArgs) -> ToolOutcome:
        scenario = self._content.scenario
        npc = next((item for item in scenario.npcs if item.id == args.npc_id), None)
        if npc is None:
            raise ToolCallError("unknown_npc", allowed=[item.id for item in scenario.npcs])
        target = next((scene for scene in scenario.scenes if scene.id == args.scene_id), None)
        if target is None:
            raise ToolCallError("unknown_scene", allowed=[scene.id for scene in scenario.scenes])
        previous = effective_location(npc, state)
        if previous == target.id:
            return ToolOutcome(
                result={"npc": args.npc_id, "scene": target.id, "already_there": True}
            )
        return ToolOutcome(
            events=[NpcMoved(npc_id=args.npc_id, scene_id=target.id, from_scene=previous)],
            result={
                "npc": args.npc_id,
                "scene": target.id,
                "from_scene": previous,
                "instruction": (
                    "The NPC is now present in that scene and absent from the previous one. "
                    "Narrate their arrival or departure only where the players can see it."
                ),
            },
        )

    def kill_npc(self, state: SessionState, args: KillNpcArgs) -> ToolOutcome:
        npc_ids = [npc.id for npc in self._content.scenario.npcs]
        if args.npc_id not in npc_ids:
            raise ToolCallError("unknown_npc", allowed=npc_ids)
        if state.npc_status.get(args.npc_id) == "dead":
            return ToolOutcome(result={"npc": args.npc_id, "already_dead": True})
        return ToolOutcome(
            events=[NpcDied(npc_id=args.npc_id)],
            result={"npc": args.npc_id, "status": "dead"},
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
            if any(
                owned.item_id == item.id for items in state.inventory.values() for owned in items
            ):
                return ToolOutcome(result={"already_granted": item.id})
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
        if (restriction := args.restriction()) is not None:
            raise ToolCallError(restriction)
        actor_id = self._actor(state, args.actor_id)
        actor = state.rules.actor(actor_id)
        if args.resource not in actor.resources:
            raise ToolCallError("unknown_resource", allowed=sorted(actor.resources))
        if args.delta == 0:
            raise ToolCallError("delta_is_zero")
        action: Action
        if args.resource == HIT_POINTS:
            action = DamageAction(target_id=actor_id, damage=str(-args.delta))
        else:
            action = ResourceAction(actor_id=actor_id, resource=args.resource, delta=args.delta)
        events, after = self._engine.run(state.rules, action, self._rng)
        resource = after.actor(actor_id).resources[args.resource]
        return ToolOutcome(
            events=list(events),
            result={"resource": args.resource, "current": resource.current, "max": resource.max},
        )

    def cast_cure_wounds(self, state: SessionState, args: CastCureWoundsArgs) -> ToolOutcome:
        if state.rules.combat is not None:
            raise ToolCallError("combat_casting_not_supported")
        caster = state.rules.actor(self._actor(state, args.caster_id, party_only=True))
        target = state.rules.actor(self._actor(state, args.target_id, party_only=True))
        seat = self._setup.seat(caster.id)
        capability = seat.cure_wounds if seat is not None else None
        if capability is None or not capability.prepared:
            raise ToolCallError("cure_wounds_not_prepared")
        if (
            caster.hp is None
            or caster.hp.current == 0
            or incapacitated_by(self._engine.ruleset, caster) is not None
        ):
            raise ToolCallError("caster_cannot_act")
        if target.hp is None:
            raise ToolCallError("target_has_no_hit_points")
        slot = caster.resources.get(FIRST_LEVEL_SLOT)
        if slot is None or slot.current < 1:
            raise ToolCallError("no_first_level_spell_slot")

        cost = ResourceAction(actor_id=caster.id, resource=FIRST_LEVEL_SLOT, delta=-1)
        heal = HealAction(target_id=target.id, source_id=caster.id, amount=capability.healing_dice)
        # Разрешаем оба действия на временном состоянии. Оркестратор фиксирует весь пакет.
        spent, after_cost = self._engine.run(state.rules, cost, self._rng)
        healing = self._engine.resolve(after_cost, heal, self._rng)
        slots_remaining = after_cost.actor(caster.id).resources[FIRST_LEVEL_SLOT].current
        rolled = next(event for event in healing if isinstance(event, DiceRolled))
        healed = next(event for event in healing if isinstance(event, Healed))
        return ToolOutcome(
            events=[*spent, *healing],
            result={
                "spell": capability.spell_id,
                "caster_id": caster.id,
                "target_id": target.id,
                "first_level_slots_remaining": slots_remaining,
                "healing_roll": str(rolled.roll),
                "healed": healed.amount,
                "hp_after": healed.hp_after,
                "removed_conditions": [
                    event.condition for event in healing if isinstance(event, ConditionRemoved)
                ],
            },
        )

    def note_fact(self, state: SessionState, args: NoteFactArgs) -> ToolOutcome:
        text = args.text.strip()
        if not text:
            raise ToolCallError("empty_fact")
        return ToolOutcome(events=[FactNoted(text=text[:MAX_FACT_LENGTH])], result={"noted": True})

    # --- Скиллы классов ---

    def protect(self, state: SessionState, args: ProtectArgs) -> ToolOutcome:
        protector = state.rules.actor(self._actor(state, args.protector_id, party_only=True))
        if "protect" not in protector.skills:
            raise ToolCallError("skill_not_available", actor_id=protector.id, skill="protect")
        if state.rules.combat is None:
            raise ToolCallError(
                "combat_required",
                instruction=(
                    "Protection is a combat action on the fighter's own turn. In combat the "
                    "player picks it from the combat options; do not resolve it by narration."
                ),
            )
        allies = allies_of(self._engine.ruleset, state.rules, protector.id)
        target = None
        if args.target_id is not None:
            target = next((ally for ally in allies if ally.id == args.target_id), None)
            if target is None:
                raise ToolCallError("invalid_protect_target", allowed=[ally.id for ally in allies])
        elif args.target_name:
            name = args.target_name.strip().casefold()
            target = next((ally for ally in allies if ally.name.casefold() == name), None)
            if target is None:
                raise ToolCallError(
                    "invalid_protect_target", allowed=[ally.name for ally in allies]
                )
        else:
            # Без явной цели — самый раненый живой союзник (наименьшие текущие хиты).
            target = min(allies, key=lambda ally: ally.hp.current if ally.hp else 0, default=None)
        if target is None:
            raise ToolCallError("no_ally_to_protect")
        action = ProtectAction(actor_id=protector.id, target_id=target.id)
        events = self._engine.resolve(state.rules, action, self._rng)
        return ToolOutcome(
            events=list(events),
            result={
                "protector_id": protector.id,
                "target_id": target.id,
                "instruction": (
                    "Narrate the shield cover as is: until the protector's next turn, attacks "
                    "on the covered ally roll against the protector's AC and the protector "
                    "takes the damage. Do not roll attacks or damage yourself."
                ),
            },
        )

    def stealth(self, state: SessionState, args: StealthArgs) -> ToolOutcome:
        actor = state.rules.actor(self._actor(state, args.actor_id, party_only=True))
        if "stealth" not in actor.skills:
            raise ToolCallError("skill_not_available", actor_id=actor.id, skill="stealth")
        action = HideAction(actor_id=actor.id)
        events = self._engine.resolve(state.rules, action, self._rng)
        return ToolOutcome(
            events=list(events),
            result={
                "actor_id": actor.id,
                "hidden": True,
                "instruction": (
                    "Narrate the hiding as is. While hidden: advantage on their own attack "
                    "rolls, disadvantage on attacks against them, enemies do not target them. "
                    "Hiding ends right after their own attack. In combat hiding costs the "
                    "action and is chosen via the combat options."
                ),
            },
        )

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
        "Mark a clue, handout, item or lore entry as discovered by the players. "
        "A clue gated by a prepared check is revealed automatically on success; "
        "do not call reveal for it.",
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
        "move_npc",
        "Move an NPC to another scene when they travel on their own or are escorted by the "
        "party. NPC presence is authoritative: an NPC appears only in their recorded scene. "
        "The party changing scene does NOT move NPCs — move escorted NPCs explicitly.",
        MoveNpcArgs,
        DmTools.move_npc,
    ),
    _Tool(
        "kill_npc",
        "Record that an NPC dies, in combat or in your narration. NPC death is "
        "authoritative: a dead NPC is a corpse and must never be described as alive, "
        "speaking or acting again.",
        KillNpcArgs,
        DmTools.kill_npc,
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
        "Change a character's resource outside combat, such as damage from a fall. "
        "HP increases and spell slot changes require a supported typed action.",
        AdjustResourceArgs,
        DmTools.adjust_resource,
    ),
    _Tool(
        CURE_WOUNDS_TOOL,
        "Cast prepared first-level Cure Wounds outside combat. The engine spends one slot "
        "and rolls healing in a single operation.",
        CastCureWoundsArgs,
        DmTools.cast_cure_wounds,
    ),
    _Tool(
        "note_fact",
        "Remember an important fact for later: a promise, a deal, a name, a decision.",
        NoteFactArgs,
        DmTools.note_fact,
    ),
    _Tool(
        "protect",
        "A fighter with the protect skill covers an ally on their combat turn instead of "
        "attacking: attacks on the ally hit the protector's AC and the protector takes the "
        "damage until their next turn. Without a target it covers the most wounded ally. "
        "Combat only; in combat the player normally picks it from the combat options.",
        ProtectArgs,
        DmTools.protect,
    ),
    _Tool(
        "stealth",
        "A rogue with the stealth skill hides. Outside combat this sets the hidden state "
        "(advantage on their own attacks, disadvantage on attacks against them, enemies do "
        "not target them; ends when they attack). In combat hiding costs the action and is "
        "chosen via the combat options.",
        StealthArgs,
        DmTools.stealth,
    ),
)
