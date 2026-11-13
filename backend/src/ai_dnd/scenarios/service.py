"""Загрузка сценариев и библиотек статблоков из YAML, проверка ссылок внутри сценария."""

import dataclasses
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary, Scenario, SceneCheck, Statblock


@dataclass(frozen=True, slots=True)
class ScenarioIssue:
    """Проблема сценария: код, место (``scenes.tavern.exits[0]``), параметры."""

    code: str
    path: str
    params: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value!r}" for name, value in self.params.items())
        return f"{self.path}: {self.code}" + (f" ({details})" if details else "")


class ScenarioError(Exception):
    """Сценарий или библиотека статблоков составлены неверно. Проблемы — в ``issues``."""

    code = "invalid_scenario"

    def __init__(self, issues: Sequence[ScenarioIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"{self.code}: " + "; ".join(str(issue) for issue in self.issues))


# --- Загрузка ---


def read_yaml(path: Path) -> Any:
    # safe_load: в файле только данные, никаких объектов Python.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_bestiary(path: Path, ruleset: Ruleset | None = None) -> Bestiary:
    """Прочитать библиотеку статблоков. С набором правил — ещё и проверить по нему."""
    bestiary = Bestiary.model_validate(read_yaml(path))
    if ruleset is not None:
        issues = find_bestiary_issues(bestiary, ruleset)
        if issues:
            raise ScenarioError(issues)
    return bestiary


def load_scenario(
    path: Path,
    bestiaries: Iterable[Bestiary] = (),
    ruleset: Ruleset | None = None,
) -> Scenario:
    """Прочитать сценарий и проверить все ссылки внутри него.

    Если что-то не так — ``ScenarioError`` со всеми проблемами сразу.
    """
    scenario = Scenario.model_validate(read_yaml(path))
    issues = find_scenario_issues(scenario, bestiaries, ruleset)
    if issues:
        raise ScenarioError(issues)
    return scenario


def resolve_statblock(
    scenario: Scenario, ref: str, bestiaries: Iterable[Bestiary] = ()
) -> Statblock | None:
    """Статблок по ссылке: ``srd:goblin`` — из библиотеки, ``cult_leader`` — из сценария."""
    if ":" not in ref:
        return scenario.statblocks.get(ref)
    library_id, monster_id = ref.split(":", 1)
    for bestiary in bestiaries:
        if bestiary.id == library_id:
            return bestiary.monsters.get(monster_id)
    return None


# --- Проверки ---


def find_bestiary_issues(bestiary: Bestiary, ruleset: Ruleset) -> list[ScenarioIssue]:
    """Статблоки должны говорить на языке набора правил: его проверки, спасброски, типы урона."""
    issues: list[ScenarioIssue] = []
    if bestiary.ruleset != ruleset.ref:
        issues.append(
            ScenarioIssue(
                "ruleset_mismatch", "ruleset", {"expected": bestiary.ruleset, "actual": ruleset.ref}
            )
        )
    for monster_id, statblock in bestiary.monsters.items():
        issues.extend(_statblock_issues(statblock, f"monsters.{monster_id}", ruleset))
    return issues


def find_scenario_issues(
    scenario: Scenario,
    bestiaries: Iterable[Bestiary] = (),
    ruleset: Ruleset | None = None,
) -> list[ScenarioIssue]:
    """Все проблемы сценария. Пустой список — по сценарию можно играть.

    Без набора правил проверяются только ссылки внутри сценария; с набором — ещё и то,
    что навыки, спасброски и типы урона в нём существуют.
    """
    libraries = list(bestiaries)
    info = scenario.scenario
    issues: list[ScenarioIssue] = []

    groups: dict[str, Sequence[Any]] = {
        "scenes": scenario.scenes,
        "npcs": scenario.npcs,
        "items": scenario.items,
        "clues": scenario.clues,
        "handouts": scenario.handouts,
        "encounters": scenario.encounters,
        "lore": scenario.lore,
    }
    ids = {name: [entity.id for entity in entities] for name, entities in groups.items()}
    for name, group_ids in ids.items():
        issues.extend(
            ScenarioIssue("duplicate_id", f"{name}.{entity_id}")
            for entity_id, count in Counter(group_ids).items()
            if count > 1
        )
    known = {name: set(group_ids) for name, group_ids in ids.items()}
    revealable = known["clues"] | known["handouts"]

    if ruleset is not None and info.ruleset != ruleset.ref:
        issues.append(
            ScenarioIssue(
                "ruleset_mismatch",
                "scenario.ruleset",
                {"expected": info.ruleset, "actual": ruleset.ref},
            )
        )
    if info.start_scene not in known["scenes"]:
        issues.append(
            ScenarioIssue("unknown_scene", "scenario.start_scene", {"id": info.start_scene})
        )

    for scene in scenario.scenes:
        path = f"scenes.{scene.id}"
        references = {
            "exits": (scene.exits, "scenes", "unknown_scene"),
            "npcs": (scene.npcs, "npcs", "unknown_npc"),
            "items": (scene.items, "items", "unknown_item"),
            "clues": (scene.clues, "clues", "unknown_clue"),
            "handouts": (scene.handouts, "handouts", "unknown_handout"),
            "encounters": (scene.encounters, "encounters", "unknown_encounter"),
        }
        for field, (targets, group, code) in references.items():
            issues.extend(
                ScenarioIssue(code, f"{path}.{field}[{index}]", {"id": target})
                for index, target in enumerate(targets)
                if target not in known[group]
            )
        if scene.id in scene.exits:
            issues.append(ScenarioIssue("exit_to_itself", f"{path}.exits"))
        for check in scene.checks:
            issues.extend(_check_issues(check, f"{path}.checks.{check.id}", revealable, ruleset))
        issues.extend(
            ScenarioIssue("duplicate_id", f"{path}.checks.{check_id}")
            for check_id, count in Counter(check.id for check in scene.checks).items()
            if count > 1
        )

    for npc in scenario.npcs:
        if npc.statblock is not None and _missing(scenario, npc.statblock, libraries):
            issues.append(
                ScenarioIssue(
                    "unknown_statblock", f"npcs.{npc.id}.statblock", {"ref": npc.statblock}
                )
            )
    for encounter in scenario.encounters:
        issues.extend(
            ScenarioIssue(
                "unknown_statblock",
                f"encounters.{encounter.id}.creatures[{index}].statblock",
                {"ref": group.statblock},
            )
            for index, group in enumerate(encounter.creatures)
            if _missing(scenario, group.statblock, libraries)
        )

    if ruleset is not None:
        for statblock_id, statblock in scenario.statblocks.items():
            issues.extend(_statblock_issues(statblock, f"statblocks.{statblock_id}", ruleset))

    if scenario.map is not None:
        issues.extend(
            ScenarioIssue("unknown_scene", f"map.nodes.{scene_id}", {"id": scene_id})
            for scene_id in scenario.map.nodes
            if scene_id not in known["scenes"]
        )

    issues.extend(
        ScenarioIssue("unreachable_scene", f"scenes.{scene_id}")
        for scene_id in _unreachable_scenes(scenario)
    )
    return issues


def _missing(scenario: Scenario, ref: str, bestiaries: Iterable[Bestiary]) -> bool:
    return resolve_statblock(scenario, ref, bestiaries) is None


def _check_issues(
    check: SceneCheck, path: str, revealable: set[str], ruleset: Ruleset | None
) -> Iterator[ScenarioIssue]:
    if (check.skill is None) == (check.save is None):
        # Нужно ровно одно из двух: навык или спасбросок.
        yield ScenarioIssue("needs_skill_or_save", path)
    if check.dc is None and check.difficulty is None:
        yield ScenarioIssue("needs_difficulty", path)
    if ruleset is not None:
        if check.skill is not None and check.skill not in ruleset.checks:
            yield ScenarioIssue("unknown_check", f"{path}.skill", {"id": check.skill})
        if check.save is not None and check.save not in ruleset.saves:
            yield ScenarioIssue("unknown_save", f"{path}.save", {"id": check.save})
    for outcome in ("on_success", "on_failure"):
        result = getattr(check, outcome)
        if result is None:
            continue
        for index, target in enumerate(result.reveal):
            if target not in revealable:
                yield ScenarioIssue(
                    "unknown_reveal", f"{path}.{outcome}.reveal[{index}]", {"id": target}
                )


def _statblock_issues(statblock: Statblock, path: str, ruleset: Ruleset) -> Iterator[ScenarioIssue]:
    for check_id in statblock.checks:
        if check_id not in ruleset.checks:
            yield ScenarioIssue("unknown_check", f"{path}.checks.{check_id}")
    for save_id in statblock.saves:
        if save_id not in ruleset.saves:
            yield ScenarioIssue("unknown_save", f"{path}.saves.{save_id}")

    known_types = ruleset.damage_types
    for attack_id, attack in statblock.attacks.items():
        if attack.damage_type is not None and attack.damage_type not in known_types:
            yield ScenarioIssue(
                "unknown_damage_type",
                f"{path}.attacks.{attack_id}.damage_type",
                {"id": attack.damage_type},
            )
    for trait in ("resistances", "vulnerabilities", "immunities"):
        for index, damage_type in enumerate(getattr(statblock, trait)):
            if damage_type not in known_types:
                yield ScenarioIssue(
                    "unknown_damage_type", f"{path}.{trait}[{index}]", {"id": damage_type}
                )


def _unreachable_scenes(scenario: Scenario) -> list[str]:
    """Сцены, в которые нельзя попасть из стартовой по переходам."""
    exits = {scene.id: scene.exits for scene in scenario.scenes}
    start = scenario.scenario.start_scene
    if start not in exits:
        return []
    reached = {start}
    queue = [start]
    while queue:
        for target in exits[queue.pop()]:
            if target in exits and target not in reached:
                reached.add(target)
                queue.append(target)
    return [scene.id for scene in scenario.scenes if scene.id not in reached]
