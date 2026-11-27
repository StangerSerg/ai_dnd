"""Сборщик контекста v1 — без поиска (архитектура, разделы 9.1 и 9.4).

Промпт собирается слоями, от неизменного к меняющемуся, чтобы у провайдера работал кэш
начала промпта:

1. системное сообщение — роль ведущего, правила, обзор сценария, отряд, стиль; одно
   и то же на всю сессию;
2. история — прошлые ходы в «чистом» виде: действия игроков и рассказ ведущего;
   только дописывается в конец;
3. последнее сообщение — текущая сцена, состояние отряда и действия игроков; меняется
   каждый ход.

Шаблоны промптов — на английском (раздел 18.3), тексты сценария — на языке игры.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ai_dnd.game.content import GameContent
from ai_dnd.game.decision import TurnDecision
from ai_dnd.game.presence import is_dead, is_present
from ai_dnd.game.session import (
    InventoryItem,
    SessionSetup,
    SessionState,
    attack_cost_id,
    item_stock,
)
from ai_dnd.game.spells import CURE_WOUNDS_TOOL, FIRST_LEVEL_SLOT
from ai_dnd.game.tones import TONE_INSTRUCTIONS
from ai_dnd.i18n import localize
from ai_dnd.rules.combat import incapacitated_by
from ai_dnd.rules.engine import BUFF_CONDITION_PREFIX, ITEM_CONDITION_PREFIX
from ai_dnd.rules.ruleset import RollBands, RollVsTarget
from ai_dnd.rules.state import HIDDEN, CombatState
from ai_dnd.rulesets.glossary import translation_lines
from ai_dnd.scenarios.schemas import Scene
from ai_dnd.scenarios.service import resolve_statblock

_PROMPTS = Path(__file__).parent / "prompts"
_ENV = Environment(
    loader=FileSystemLoader(_PROMPTS),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
    autoescape=False,
)

LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}

# Ключи — канонические английские метки GameMaster._mandatory_outcomes (аудит зависит
# от них); локализация эпитета происходит только при рендере чек-листа.
_WOUND_EPITHETS = {
    "ru": {
        "devastating blow": "сокрушительный удар",
        "heavy wound": "тяжёлое ранение",
        "serious wound": "серьёзное ранение",
        "light wound": "лёгкая рана",
        "wound": "рана",
    },
}

IMPROVISATION_RULES = {
    "strict": (
        "strict. Use only what the current scene has: its NPCs, clues, items and "
        "encounters. Move only through its exits. No invented items."
    ),
    "moderate": (
        "moderate. You may use anything from the scenario and add colour: background "
        "characters without game effect, minor improvised items. Move only through exits "
        "of the current scene."
    ),
    "free": (
        "free. You may move the party to any scene of the scenario and invent items and "
        "details, as long as the story stays consistent with the scenario."
    ),
}


@dataclass(frozen=True, slots=True)
class ActionLine:
    actor_id: str
    name: str
    text: str


class ContextBuilder:
    def __init__(self, content: GameContent, setup: SessionSetup) -> None:
        self._content = content
        self._setup = setup
        self._language = setup.language

    # --- Слой 1: неизменная часть ---

    @cached_property
    def system_prompt(self) -> str:
        return self._render_system(include_style=True, private_context=False)

    def _render_system(self, *, include_style: bool, private_context: bool) -> str:
        ruleset = self._content.ruleset
        scenario = self._content.scenario
        language = self._language
        glossary = (
            translation_lines(self._content.glossary.terms, "en", language)
            if language != "en"
            else []
        )
        values: dict[str, Any] = {
            "language_name": LANGUAGE_NAMES.get(language, language),
            "improvisation_rules": IMPROVISATION_RULES[self._setup.improvisation],
            "ruleset_name": localize(ruleset.name, "en"),
            "resolution": _resolution(self._content),
            "difficulty": ", ".join(
                f"{level.value} {value}"
                for level, value in sorted(ruleset.difficulty.items(), key=lambda item: item[1])
            ),
            "checks": _labels(ruleset.checks, language),
            "saves": _labels(ruleset.saves, language),
            "conditions": _labels(ruleset.conditions, language),
            "ruleset_summary": localize(ruleset.summary, language) if ruleset.summary else "",
            "glossary": glossary,
            "scenario": scenario.scenario
            if private_context
            else {"title": scenario.scenario.title},
            "scenes": scenario.scenes if private_context else (),
            "npcs": scenario.npcs if private_context else (),
            "seats": self._setup.seats,
            "include_style": include_style,
            "private_context": private_context,
            "items_enabled": scenario.scenario.items_enabled,
        }
        # Планировщик отвечает только JSON: прозаический стиль ему не нужен и противоречил
        # бы протоколу решения; рассказчик получает стиль целиком.
        if include_style:
            values["style"] = _render(
                f"style_{language}.j2" if language in ("ru", "en") else "style_en.j2"
            )
            tone_instruction = TONE_INSTRUCTIONS[self._setup.tone]
            if tone_instruction:
                values["style"] += "\n\n" + _render("tone.j2", instruction=tone_instruction)
        return _render("dm_system.j2", **values)

    # --- Слой 2: история ---

    def history_entry(self, actions: Sequence[ActionLine]) -> str:
        """Как ход выглядит в истории: только действия, без снимка сцены."""
        if not actions:
            return "(Session start)"
        return "Players' actions:\n" + "\n".join(
            f"- {action.name}: {action.text}" for action in actions
        )

    def summary_entry(self, summary: str) -> str:
        """Слой 4: сводка «что было раньше» перед дословной историей (раздел 9.3)."""
        return "# Earlier in this session (recap of turns before the recent ones)\n" + summary

    @cached_property
    def decision_prompt(self) -> str:
        schema = json.dumps(
            TurnDecision.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            self._render_system(include_style=False, private_context=True)
            + "\n\n"
            + _render(
                "decision_system.j2",
                schema=schema,
                cure_wounds_tool=CURE_WOUNDS_TOOL,
                items_enabled=self._content.scenario.scenario.items_enabled,
            )
        )

    def decision_message(
        self,
        state: SessionState,
        actions: Sequence[tuple[int, ActionLine]],
        completed: Sequence[dict[str, Any]],
        *,
        moved: bool,
        problem: str = "",
        fragments: Sequence[str] = (),
    ) -> str:
        # Действия рендерятся один раз — блоком с авторитетными индексами ниже;
        # снимок сцены идёт без них, чтобы текст игрока не дублировался. Формат
        # «input_index=N, actor_id=X: » сохранён: его разбирает FakeProvider.
        snapshot = self._render_turn(state, (), private_context=True, fragments=fragments)
        indexed = "\n".join(
            f"- input_index={index}, actor_id={action.actor_id}: {action.name} — {action.text}"
            for index, action in actions
        )
        ledger = json.dumps(completed, ensure_ascii=False, separators=(",", ":"))
        prepared = []
        for seat in self._setup.seats:
            if seat.cure_wounds is None or not seat.cure_wounds.prepared:
                continue
            actor = state.rules.actor(seat.actor_id)
            if seat.cure_wounds.consumes_slot:
                slot = actor.resources.get(FIRST_LEVEL_SLOT)
                available = str(slot.current if slot is not None else 0)
            else:
                available = "unlimited (no spell slot spent)"
            prepared.append(f"- {actor.id}: {available}")
        casters = "\n".join(prepared) or "- none"
        return (
            f"{snapshot}\n\n# Unresolved original inputs (authoritative indices)\n{indexed}"
            "\n\n# Prepared Cure Wounds casters "
            f"(actor id: first-level slots remaining or unlimited)\n{casters}"
            f"\n\n# Already committed operations and actual outcomes\n{ledger}"
            f"\n\n# Movement already completed in this turn\n{moved}"
            "\n\n# Encounters already started in this session (do not start again)\n"
            + json.dumps(state.started_encounters, ensure_ascii=False, separators=(",", ":"))
            + (
                f"\n\n# Previous decision error; correct only unresolved inputs\n{problem}"
                if problem
                else ""
            )
        )

    def narration_message(
        self,
        state: SessionState,
        actions: Sequence[ActionLine],
        completed: Sequence[dict[str, Any]],
        *,
        fragments: Sequence[str] = (),
    ) -> str:
        ledger = json.dumps(completed, ensure_ascii=False, separators=(",", ":"))
        combat = state.rules.combat
        roster = ""
        if combat is not None:
            lines = []
            counts: dict[str, dict[str, int]] = {}
            for actor_id in combat.order:
                actor = state.rules.actors[actor_id]
                side = combat.sides[actor_id]
                condition = incapacitated_by(self._content.ruleset, actor)
                status = (
                    "dead"
                    if actor.dead
                    else "fled"
                    if actor_id in combat.fled
                    else "incapacitated"
                    if condition is not None or (actor.hp is not None and actor.hp.current == 0)
                    else "alive"
                )
                side_counts = counts.setdefault(
                    side,
                    {
                        "total": 0,
                        "present_alive": 0,
                        "able_to_act": 0,
                        "incapacitated": 0,
                        "dead": 0,
                        "fled": 0,
                    },
                )
                side_counts["total"] += 1
                side_counts["able_to_act" if status == "alive" else status] += 1
                if status in ("alive", "incapacitated"):
                    side_counts["present_alive"] += 1
                lines.append(
                    f"- {actor.name} (id: {actor_id}, side: {side}, "
                    f"HP: {actor.hp.current if actor.hp else '—'}, status: {status})"
                )
            roster = (
                "\n\n# Current combat roster and counts (authoritative)\n"
                + "\n".join(lines)
                + "\n"
                + json.dumps(counts, ensure_ascii=False, separators=(",", ":"))
                + "\nThe fight has begun; no attacks or damage are resolved by this narration."
            )
        return (
            self.public_turn_message(state, actions, fragments=fragments)
            + "\n\n# Committed events and actual tool outcomes (authoritative)\n"
            + ledger
            + roster
            + "\n\n# Encounters already started in this session\n"
            + json.dumps(state.started_encounters, ensure_ascii=False, separators=(",", ":"))
            + "\n\nNarrate only the current scene and these committed outcomes. "
            "Reflect every committed outcome that changed state: damage, healing, "
            "death, a condition applied or removed, a resource spent; do not omit a "
            "committed effect. "
            "Do not describe a move, successful check, hit, reward, discovery, "
            "healing, regained consciousness, death or flight unless the committed outcomes "
            "show it. If requested healing has no committed outcome, say it was not resolved. "
            "If no roll or effect happened, leave uncertain attempts unresolved. "
            "When describing where the party is or can go, name only the current scene and "
            "its Exits — the only reachable locations; never describe presence in or scouting "
            "of a location without a committed change_scene to it. "
            "You may answer players' questions from the public background fragments above "
            "(known lore, not a new discovery); never reveal DM-only information."
        )

    # --- Слой 3: текущий ход ---

    def turn_message(
        self,
        state: SessionState,
        actions: Sequence[ActionLine],
        *,
        opening: bool = False,
        fragments: Sequence[str] = (),
    ) -> str:
        """Full scene snapshot for the planner and legacy callers."""
        return self._render_turn(
            state, actions, private_context=True, opening=opening, fragments=fragments
        )

    def public_turn_message(
        self,
        state: SessionState,
        actions: Sequence[ActionLine],
        *,
        opening: bool = False,
        fragments: Sequence[str] = (),
    ) -> str:
        """Player-facing scene snapshot for opening and ordinary narration."""
        return self._render_turn(
            state, actions, private_context=False, opening=opening, fragments=fragments
        )

    def _render_turn(
        self,
        state: SessionState,
        actions: Sequence[ActionLine],
        *,
        private_context: bool,
        opening: bool = False,
        scene_update: bool = False,
        fragments: Sequence[str] = (),
    ) -> str:
        return _render(
            "turn.j2",
            **self._scene_view(state, private_context=private_context),
            private_context=private_context,
            opening=opening,
            actions=actions,
            scene_update=scene_update,
            fragments=fragments,
        )

    def scene_update_message(self, state: SessionState) -> str:
        """Full current scene for a change_scene tool result, without a new player turn."""
        return self._render_turn(state, (), private_context=True, scene_update=True)

    def combat_message(
        self,
        state: SessionState,
        log: Sequence[str],
        *,
        roster: CombatState,
        fled: set[str],
        terminal_events: Sequence[str],
        ended: bool,
        defeated: bool = False,
        mandatory: Sequence[str] = (),
        misses: int = 0,
    ) -> str:
        scene = self._scene(state.scene_id)
        location = f"{scene.title} (id: {scene.id})"
        if scene.read_aloud:
            location += (
                "\nInitial setting/scenery only (current participants and combat log "
                f"override any actors mentioned): {scene.read_aloud}"
            )
        lines = "\n".join(f"- {line}" for line in log)
        party = "\n".join(f"- {member['name']}: HP {member['hp']}" for member in self._party(state))
        participants = []
        counts: dict[str, dict[str, int]] = {}
        for actor_id in roster.order:
            actor = state.rules.actors[actor_id]
            side = roster.sides[actor_id]
            condition = incapacitated_by(self._content.ruleset, actor)
            if actor.dead:
                status = "dead"
                category = "dead"
            elif actor_id in fled:
                status = "fled"
                category = "fled"
            elif condition is not None:
                status = f"incapacitated ({condition})"
                category = "incapacitated"
            elif actor.hp is not None and actor.hp.current == 0:
                status = "incapacitated (0 HP)"
                category = "incapacitated"
            else:
                status = "alive"
                category = "able_to_act"
            side_counts = counts.setdefault(
                side,
                {
                    "total": 0,
                    "present_alive": 0,
                    "able_to_act": 0,
                    "incapacitated": 0,
                    "dead": 0,
                    "fled": 0,
                },
            )
            side_counts["total"] += 1
            side_counts[category] += 1
            if category in ("able_to_act", "incapacitated"):
                side_counts["present_alive"] += 1
            participants.append(
                f"- {actor.name} (id: {actor_id}, side: {side}): "
                f"HP {actor.hp.current if actor.hp else '?'}, status: {status}"
                + (", hidden" if actor.condition(HIDDEN) is not None else "")
                + (f", protected by {actor.protected_by}" if actor.protected_by else "")
            )
        participants_text = "\n".join(participants)
        counts_text = "\n".join(
            f"- {side}: total={values['total']}, present_alive={values['present_alive']} "
            f"(includes incapacitated), able_to_act={values['able_to_act']}, "
            f"incapacitated={values['incapacitated']}, dead={values['dead']}, "
            f"fled={values['fled']}"
            for side, values in counts.items()
        )
        if ended and defeated:
            ending = (
                "\nThe party has been defeated: every hero is unconscious or dead. "
                "This is the scenario's FAILURE outcome. Narrate a final closing scene "
                "(two or three paragraphs) grounded ONLY in what the players have "
                "actually experienced and learned in play: the current scene, the quest "
                "the heroes undertook, the foes they faced, the people and places they "
                "already know and tried to help, and the visible local consequences of "
                "their failure — what befalls the fallen heroes here and now. "
                "SECRECY: never reveal DM-only material. Do not name or describe "
                "undiscovered NPCs, hidden plot twists, the secret scenario synopsis, "
                "villains or factions the players have not met, or hidden locations and "
                "artifacts they have not learned about. Do not spoil the mystery or "
                "explain what the players never discovered. End on a conclusive final "
                "phrase — a closing narration, NOT 'hand control back to the players' "
                "and NOT a list of options. Do not invent a rescue, revival or reversal "
                "unless the scenario itself provides one AND the players already know "
                "of it. Keep the current scene and its terrain."
            )
        elif ended:
            ending = (
                "\nThe fight is over. Describe the aftermath briefly and hand control back to "
                "the players. If the combat log shows item grants (🎒 ... gets:), that victory "
                "loot is already committed by the mechanics: narrate the party finding and "
                "collecting exactly those items, never grant or duplicate them again."
            )
        else:
            ending = ""
        # Сценарные ярлыки поверх дефолтных: только рендер, канонические ключи аудита
        # в mandatory не меняются.
        epithets = {
            **_WOUND_EPITHETS.get(self._language, {}),
            **self._content.scenario.scenario.wound_epithets,
        }
        mandatory_text = (
            "\n".join(
                f"{i}. {_localize_epithets(item, epithets)}" for i, item in enumerate(mandatory, 1)
            )
            if mandatory
            else "- none"
        )
        mandatory_count = len(mandatory)
        recap = ""
        checklist_tail = ""
        if mandatory:
            if not ended:
                recap = (
                    "\n\nThis narration recaps the round that just ended; the fight is still "
                    "active. Report every blow that landed, each with its wound epithet, "
                    "in the numbered (initiative) order below, before any scene-setting, "
                    "atmosphere or misses."
                )
            checklist_tail = (
                recap
                + "\n\n# Mandatory outcomes — final checklist (coverage contract)\n"
                + f"Narrate all {mandatory_count} numbered items below, each individually "
                "and in order. Flowing prose is the style; this checklist is the contract: "
                "no item may be skipped, merged or replaced by a miss or a state "
                "description.\n" + mandatory_text
            )
        return (
            f"# Current location (authoritative)\n{location}\n\n"
            "# Combat log since your last description\n"
            f"{lines}\n\n# Party\n{party}\n"
            f"\n# Combat status\n{'ended' if ended else 'active'}\n"
            f"\n# Side counts (authoritative; total = present_alive + dead + fled)\n{counts_text}\n"
            f"\n# Participants (authoritative current status)\n{participants_text}\n"
            "\n# Terminal events in this log\n"
            + (
                "\n".join(f"- {event}" for event in terminal_events)
                if terminal_events
                else "- none"
            )
            + "\n\n# Mandatory outcomes (each MUST be narrated individually)\n"
            + f"There are exactly {mandatory_count} mandatory outcomes below. Damage items "
            "already carry the wound-severity epithet in the narration language.\n"
            + mandatory_text
            + f"\n\n# Misses in this log: {misses} (may be summarized in one phrase)\n"
            "\nDescribe these events as one vivid, flowing artistic scene of two or "
            "three paragraphs (more if needed). Write literary prose; NEVER a list or "
            "a mechanical blow-by-blow enumeration. "
            "The checklist is a coverage contract, the prose is the style: flowing prose "
            "must still contain EVERY numbered beat — 'not a list' never means an item "
            "may be skipped or merged.\n"
            "Every death, loss of consciousness, damage dealt and heal in 'Mandatory "
            "outcomes' MUST appear individually: who did what to whom, the wound severity, "
            "and the result (wounded / downed unconscious / killed). Narrate each damage "
            "item using the exact wound-severity epithet shown in its checklist item, "
            "woven naturally into the prose (e.g. «волк вцепился в бок, оставив "
            f"{epithets.get('serious wound', 'serious wound')}»). "
            "NEVER write a numeric count of wounds like «6 ран» / «six wounds», and "
            "never state raw damage numbers — the epithet already encodes the "
            "severity. Describe each damage event "
            "separately, EVEN a minor wound. If multiple attacks hit the SAME character, "
            "describe each hit separately — do not merge them into one phrase. A killing "
            "blow / death must NEVER be skipped or replaced by a miss.\n"
            "Misses and attacks that dealt no damage are NOT mandatory item by item: "
            'collapse them into one short phrase (e.g. "ни один удар героев не достиг '
            'цели" / "the heroes\' blows all fell short"). When the log is long, '
            "compress all misses this way instead of describing each one.\n"
            "Fidelity: a hit that dealt damage in the log must not be described as a "
            "miss or deflection, and a miss must not be described as a hit. Do not "
            "invent damage, deaths, unconsciousness or outcomes that are not in "
            "'Mandatory outcomes'. Describe a death only for an actor marked DIED, "
            "and unconsciousness only for an actor marked downed/unconscious there. "
            "A wounded actor with remaining HP is still alive and fighting.\n"
            "Take side counts and participant statuses from the authoritative blocks "
            "above; do not invent additional dead, fled or present combatants. Only "
            "describe flight, death or victory if this log has the corresponding "
            "ActorFled, ActorDied or CombatEnded event. Keep this fight and its "
            "participants in the current scene and its described terrain; do not move "
            "them elsewhere or invent different terrain.\n"
            f"FINAL CHECK before you finish: all {mandatory_count} numbered mandatory "
            "outcomes must each appear in your scene — verify every numbered item, "
            "including each separate hit and every death, is depicted." + checklist_tail + ending
        )

    # --- Внутреннее ---

    def _scene_view(self, state: SessionState, *, private_context: bool) -> dict[str, Any]:
        scenario = self._content.scenario
        scene = self._scene(state.scene_id)
        npcs = [
            {
                "id": npc.id,
                "name": npc.name,
                "attitude": state.attitudes.get(npc.id, npc.attitude),
                "description": npc.description,
                "personality": npc.personality if private_context else None,
                "secrets": list(npc.secrets) if private_context else [],
            }
            for npc in scenario.npcs
            # Присутствие детерминировано: scene.npcs — суперсет по фазам, локация
            # (npc_locations поверх home) решает, кто действительно здесь.
            # Мёртвые (npc_status) идут в dead_npcs — трупы, не живые NPC.
            if is_present(npc, scene, state) and not is_dead(npc.id, state)
        ]
        dead_npcs = [
            {"id": npc.id, "name": npc.name, "description": npc.description}
            for npc in scenario.npcs
            if is_present(npc, scene, state) and is_dead(npc.id, state)
        ]
        checks = [
            {
                "id": check.id,
                "kind": "skill" if check.skill else "save",
                "subject": check.skill or check.save,
                "dc": check.dc,
                "when": check.description,
                "reveals": list(check.on_success.reveal) if check.on_success else [],
            }
            for check in scene.checks
            if private_context
        ]
        here = (*scene.clues, *scene.items, *scene.handouts)
        entities = [
            {
                "id": entity_id,
                "status": (
                    "discovered"
                    if state.is_revealed(entity_id)
                    else "hidden"
                    if private_context or not self._entity_is_public(entity_id)
                    else "visible"
                ),
                "text": self._entity_text(entity_id),
            }
            for entity_id in here
            if private_context or state.is_revealed(entity_id) or self._entity_is_public(entity_id)
        ]
        encounters = [
            {
                "id": encounter.id,
                "title": encounter.title,
                "description": encounter.description,
                "creatures": ", ".join(
                    f"{group.count}× {self._creature_name(group.statblock, group.name)}"
                    for group in encounter.creatures
                ),
            }
            for encounter in scenario.encounters
            if private_context and encounter.id in scene.encounters
        ]
        exits = [{"id": exit_id, "title": self._scene(exit_id).title} for exit_id in scene.exits]
        rest = None
        if private_context and scene.rest is not None:
            policy = scene.rest
            rest = {
                "id": policy.id,
                "name": policy.name,
                "resources": policy.resources,
                "after_encounters": policy.after_encounters,
                "status": (
                    "used"
                    if policy.id in state.used_rests
                    else "available"
                    if state.rules.combat is None
                    and any(
                        state.encounter_outcomes.get(encounter_id) == "party"
                        for encounter_id in policy.after_encounters
                    )
                    else "locked"
                ),
            }
        revealed = [
            self._entity_text(entity_id) for entity_id in state.revealed if entity_id not in here
        ]
        return {
            "scene": scene
            if private_context
            else {
                "id": scene.id,
                "title": scene.title,
                "read_aloud": scene.read_aloud,
                "dm_notes": None,
            },
            "npcs": npcs,
            "dead_npcs": dead_npcs,
            "checks": checks,
            "entities": entities,
            "encounters": encounters,
            "exits": exits,
            "rest": rest,
            "party": self._party(state),
            "revealed": revealed,
            "facts": list(state.facts) if private_context else [],
        }

    def _entity_is_public(self, entity_id: str) -> bool:
        scenario = self._content.scenario
        return any(
            entity.id == entity_id and entity.visibility == "public"
            for group in (scenario.clues, scenario.items, scenario.handouts)
            for entity in group
        )

    def _party(self, state: SessionState) -> list[dict[str, Any]]:
        # Тумблер предметов (подэтап 7): при выключенном инвентарь и экипировка
        # не попадают в контекст ведущего — предметы живут только в его памяти.
        items_enabled = self._content.scenario.scenario.items_enabled
        members = []
        for seat in self._setup.seats:
            actor = state.rules.actors[seat.actor_id]
            hp = actor.hp
            entries = {
                item.item_id: item
                for item in state.inventory.get(seat.actor_id, ())
                if item.item_id is not None
            }
            worn = state.equipment.get(seat.actor_id, {})
            conditions = ", ".join(
                self._condition_text(item.id, entries)
                for item in actor.conditions
                # item:* — служебные условия экипировки; мастер видит их строкой equipment.
                if not item.id.startswith(ITEM_CONDITION_PREFIX)
            )
            items = (
                ", ".join(
                    item.name
                    + (f" +{item.enhancement}" if item.enhancement else "")
                    + (f" ×{item.quantity}" if item.quantity > 1 else "")
                    for item in state.inventory.get(seat.actor_id, ())
                )
                if items_enabled
                else ""
            )
            equipment_parts = []
            for item_id, slot in worn.items():
                if item_id not in entries:
                    continue
                entry = entries[item_id]
                part = f"{slot}: {entry.name}" + (
                    f" +{entry.enhancement}" if entry.enhancement else ""
                )
                # Остаток боеприпасов экипированного оружия виден ведущему.
                profile = actor.attacks.get(f"item:{item_id}")
                cost = attack_cost_id(profile) if profile is not None else None
                if cost is not None and profile is not None:
                    stock = item_stock(state, seat.actor_id, cost)
                    if profile.ammo is not None:
                        ammo_name = entries[cost].name if cost in entries else cost
                        part += f" (ammo: {ammo_name} ×{stock})"
                    else:
                        part += f" (thrown, left: {stock})"
                equipment_parts.append(part)
            # Числовой боезапас атак (например, ракеты корабля): остаток ресурса
            # виден ведущему так же, как остаток предметных боеприпасов.
            for profile in actor.attacks.values():
                if profile.ammo_resource is None:
                    continue
                resource = actor.resources.get(profile.ammo_resource)
                definition = self._content.ruleset.resources.get(profile.ammo_resource)
                ammo_name = (
                    localize(definition.label, self._language)
                    if definition is not None
                    else profile.ammo_resource
                )
                left = resource.current if resource is not None else 0
                equipment_parts.append(f"{profile.name} (ammo: {ammo_name} ×{left})")
            equipment = ", ".join(equipment_parts) if items_enabled else ""
            ac = actor.ac
            if ac is not None:
                ac += sum(self._item_ac_bonus(item_id) for item_id in worn)
            protector = (
                state.rules.actors[actor.protected_by].name
                if actor.protected_by is not None and actor.protected_by in state.rules.actors
                else None
            )
            members.append(
                {
                    "id": actor.id,
                    "name": actor.name,
                    "hp": f"{hp.current}/{hp.max}" if hp else "—",
                    "ac": ac if ac is not None else "—",
                    "conditions": conditions + (" (dead)" if actor.dead else ""),
                    "items": items,
                    "equipment": equipment,
                    "skills": ", ".join(actor.skills),
                    "protected_by": protector,
                }
            )
        return members

    def _condition_text(self, condition_id: str, entries: dict[str, InventoryItem]) -> str:
        if condition_id.startswith(BUFF_CONDITION_PREFIX):
            item_id = condition_id[len(BUFF_CONDITION_PREFIX) :]
            entry = entries.get(item_id)
            return f"buff: {entry.name}" if entry else condition_id
        if condition_id in self._content.ruleset.conditions:
            return localize(self._content.ruleset.conditions[condition_id].label, self._language)
        return condition_id

    def _item_ac_bonus(self, item_id: str) -> int:
        for item in self._content.scenario.items:
            if item.id == item_id:
                return item.ac_bonus
        return 0

    def _scene(self, scene_id: str) -> Scene:
        return next(scene for scene in self._content.scenario.scenes if scene.id == scene_id)

    def _creature_name(self, ref: str, override: str | None) -> str:
        if override:
            return override
        statblock = resolve_statblock(self._content.scenario, ref, self._content.bestiaries)
        return localize(statblock.name, self._language) if statblock else ref

    def _entity_text(self, entity_id: str) -> str:
        scenario = self._content.scenario
        for clue in scenario.clues:
            if clue.id == entity_id:
                return clue.text
        for item in scenario.items:
            if item.id == entity_id:
                return f"{item.name}" + (f": {item.description}" if item.description else "")
        for handout in scenario.handouts:
            if handout.id == entity_id:
                return f"{handout.title}: {handout.text}"
        for lore in scenario.lore:
            if lore.id == entity_id:
                return f"{lore.title}: {lore.text}"
        return entity_id


def _render(template: str, **values: Any) -> str:
    return _ENV.get_template(template).render(**values).strip()


def _localize_epithets(item: str, epithets: dict[str, str]) -> str:
    # Замена только в слоте «: эпитет (последствие)», чтобы не задеть имена и '(wounded)'.
    for english, native in epithets.items():
        item = item.replace(f": {english} (", f": {native} (")
    return item


def _labels(group: dict[str, Any], language: str) -> str:
    return ", ".join(f"{key} — {localize(item.label, language)}" for key, item in group.items())


def _resolution(content: GameContent) -> str:
    resolution = content.ruleset.resolution
    if isinstance(resolution, RollVsTarget):
        text = (
            f"A check is {resolution.dice} + bonus against a target number (DC); it succeeds "
            "when the total is at least the DC."
        )
        if resolution.critical_success is not None:
            text += f" A natural {resolution.critical_success} is a critical success."
        if resolution.critical_failure is not None:
            text += f" A natural {resolution.critical_failure} is a critical failure."
        return text
    if isinstance(resolution, RollBands):
        bands = ", ".join(f"{band.min}+ {band.outcome.value}" for band in resolution.bands)
        return (
            f"A check is {resolution.dice} + bonus; the difficulty is a modifier to the roll. "
            f"Outcome by total: {bands}; below that — {resolution.below.value}."
        )
    return ""
