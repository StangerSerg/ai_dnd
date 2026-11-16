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

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ai_dnd.game.content import GameContent
from ai_dnd.game.session import SessionSetup, SessionState
from ai_dnd.i18n import localize
from ai_dnd.rules.ruleset import RollBands, RollVsTarget
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
        ruleset = self._content.ruleset
        scenario = self._content.scenario
        language = self._language
        glossary = (
            translation_lines(self._content.glossary.terms, "en", language)
            if language != "en"
            else []
        )
        return _render(
            "dm_system.j2",
            language_name=LANGUAGE_NAMES.get(language, language),
            improvisation_rules=IMPROVISATION_RULES[self._setup.improvisation],
            ruleset_name=localize(ruleset.name, "en"),
            resolution=_resolution(self._content),
            difficulty=", ".join(
                f"{level.value} {value}"
                for level, value in sorted(ruleset.difficulty.items(), key=lambda item: item[1])
            ),
            checks=_labels(ruleset.checks, language),
            saves=_labels(ruleset.saves, language),
            conditions=_labels(ruleset.conditions, language),
            ruleset_summary=localize(ruleset.summary, language) if ruleset.summary else "",
            glossary=glossary,
            scenario=scenario.scenario,
            scenes=scenario.scenes,
            npcs=scenario.npcs,
            seats=self._setup.seats,
            style=_render(f"style_{language}.j2" if language in ("ru", "en") else "style_en.j2"),
        )

    # --- Слой 2: история ---

    def history_entry(self, actions: Sequence[ActionLine]) -> str:
        """Как ход выглядит в истории: только действия, без снимка сцены."""
        if not actions:
            return "(Session start)"
        return "Players' actions:\n" + "\n".join(
            f"- {action.name}: {action.text}" for action in actions
        )

    # --- Слой 3: текущий ход ---

    def turn_message(
        self, state: SessionState, actions: Sequence[ActionLine], *, opening: bool = False
    ) -> str:
        return _render(
            "turn.j2",
            **self._scene_view(state),
            opening=opening,
            actions=actions,
        )

    def combat_message(self, state: SessionState, log: Sequence[str], *, ended: bool) -> str:
        lines = "\n".join(f"- {line}" for line in log)
        party = "\n".join(f"- {member['name']}: HP {member['hp']}" for member in self._party(state))
        enemies = "\n".join(
            f"- {actor.name}: HP {actor.hp.current if actor.hp else '?'}"
            f"{' (dead)' if actor.dead else ''}"
            for actor in state.rules.actors.values()
            if actor.id not in self._setup.party
            and state.rules.combat is not None
            and actor.id in state.rules.combat.sides
        )
        ending = (
            "\nThe fight is over. Describe the aftermath briefly and hand control back to "
            "the players."
            if ended
            else ""
        )
        return (
            "# Combat log since your last description\n"
            f"{lines}\n\n# Party\n{party}\n"
            + (f"\n# Enemies\n{enemies}\n" if enemies else "")
            + "\nDescribe these events vividly in one or two short paragraphs, as one "
            "flowing scene. Do not add attacks, damage or outcomes that are not in the log."
            + ending
        )

    # --- Внутреннее ---

    def _scene_view(self, state: SessionState) -> dict[str, Any]:
        scenario = self._content.scenario
        scene = self._scene(state.scene_id)
        npcs = [
            {
                "id": npc.id,
                "name": npc.name,
                "attitude": state.attitudes.get(npc.id, npc.attitude),
                "description": npc.description,
                "personality": npc.personality,
                "secrets": list(npc.secrets),
            }
            for npc in scenario.npcs
            if npc.id in scene.npcs
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
        ]
        here = (*scene.clues, *scene.items, *scene.handouts)
        entities = [
            {
                "id": entity_id,
                "status": "discovered" if state.is_revealed(entity_id) else "hidden",
                "text": self._entity_text(entity_id),
            }
            for entity_id in here
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
            if encounter.id in scene.encounters
        ]
        exits = [{"id": exit_id, "title": self._scene(exit_id).title} for exit_id in scene.exits]
        revealed = [
            self._entity_text(entity_id) for entity_id in state.revealed if entity_id not in here
        ]
        return {
            "scene": scene,
            "npcs": npcs,
            "checks": checks,
            "entities": entities,
            "encounters": encounters,
            "exits": exits,
            "party": self._party(state),
            "revealed": revealed,
            "facts": list(state.facts),
        }

    def _party(self, state: SessionState) -> list[dict[str, Any]]:
        members = []
        for seat in self._setup.seats:
            actor = state.rules.actors[seat.actor_id]
            hp = actor.hp
            conditions = ", ".join(
                localize(self._content.ruleset.conditions[item.id].label, self._language)
                if item.id in self._content.ruleset.conditions
                else item.id
                for item in actor.conditions
            )
            items = ", ".join(item.name for item in state.inventory.get(seat.actor_id, ()))
            members.append(
                {
                    "id": actor.id,
                    "name": actor.name,
                    "hp": f"{hp.current}/{hp.max}" if hp else "—",
                    "ac": actor.ac if actor.ac is not None else "—",
                    "conditions": conditions + (" (dead)" if actor.dead else ""),
                    "items": items,
                }
            )
        return members

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
