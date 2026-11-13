import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_dnd.rules.ruleset import Difficulty, EffectTarget, RollVsTarget, Ruleset, RulesetError
from ai_dnd.rulesets.service import find_compatibility_issues, load_ruleset
from ai_dnd.sheets.compiled import CompiledTemplate
from ai_dnd.sheets.issues import SheetIssue
from ai_dnd.sheets.schemas import SheetTemplate

ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}

# --- Встроенный набор правил ---


def test_builtin_ruleset_is_valid(srd5_ruleset: Ruleset) -> None:
    assert srd5_ruleset.ref == "dnd5e-srd@1"
    assert isinstance(srd5_ruleset.resolution, RollVsTarget)
    assert srd5_ruleset.resolution.dice == "1d20"
    # В 5e натуральная 20 на проверке — не автоматический успех.
    assert srd5_ruleset.resolution.critical_success is None


def test_builtin_difficulty_table(srd5_ruleset: Ruleset) -> None:
    assert {level.value: number for level, number in srd5_ruleset.difficulty.items()} == {
        "very_easy": 5,
        "easy": 10,
        "medium": 15,
        "hard": 20,
        "very_hard": 25,
        "nearly_impossible": 30,
    }
    assert set(srd5_ruleset.difficulty) == set(Difficulty)


def test_builtin_checks_and_saves(srd5_ruleset: Ruleset) -> None:
    abilities = {key for key, check in srd5_ruleset.checks.items() if check.ability is None}
    skills = {key: check.ability for key, check in srd5_ruleset.checks.items() if check.ability}

    assert abilities == ABILITIES
    assert len(skills) == 18
    assert (skills["stealth"], skills["athletics"], skills["perception"]) == ("dex", "str", "wis")
    assert set(srd5_ruleset.saves) == ABILITIES


def test_builtin_combat_settings(srd5_ruleset: Ruleset) -> None:
    combat = srd5_ruleset.combat

    assert combat.enabled is True
    assert (combat.attack_dice, combat.critical_hit, combat.automatic_miss) == ("1d20", 20, 1)
    assert combat.critical_damage == "double_dice"
    assert (combat.zero_hp.condition, combat.zero_hp.creatures_die) == ("unconscious", True)
    assert len(srd5_ruleset.damage_types) == 13


def test_builtin_conditions(srd5_ruleset: Ruleset) -> None:
    conditions = srd5_ruleset.conditions

    assert len(conditions) == 14
    assert {key for key, condition in conditions.items() if condition.prevents_actions} == {
        "incapacitated",
        "paralyzed",
        "petrified",
        "stunned",
        "unconscious",
    }
    poisoned = {effect.applies_to for effect in conditions["poisoned"].effects}
    assert poisoned == {EffectTarget.ATTACKS, EffectTarget.CHECKS}


def test_builtin_resources_recover_on_a_long_rest(srd5_ruleset: Ruleset) -> None:
    recovery = {
        key: (resource.recovery.rest, resource.recovery.amount)
        for key, resource in srd5_ruleset.resources.items()
        if resource.recovery is not None
    }

    assert recovery["hp"] == ("long", "full")
    assert recovery["hit_dice"] == ("long", "half")
    assert all(recovery[f"spell_slots_{level}"] == ("long", "full") for level in range(1, 10))


def test_every_builtin_label_is_in_russian_and_english(srd5_ruleset: Ruleset) -> None:
    labels: dict[str, object] = {"name": srd5_ruleset.name}
    for group in ("checks", "saves", "resources", "conditions"):
        for key, item in getattr(srd5_ruleset, group).items():
            labels[f"{group}.{key}"] = item.label
    for key, label in srd5_ruleset.damage_types.items():
        labels[f"damage_types.{key}"] = label

    incomplete = [
        place
        for place, label in labels.items()
        if not isinstance(label, dict) or set(label) != {"ru", "en"}
    ]

    assert incomplete == []


# --- Загрузка ---


def test_broken_ruleset_file_is_rejected(tmp_path: Path, content_dir: Path) -> None:
    source = content_dir / "rulesets" / "dnd5e-srd.json"
    data: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    data["combat"]["zero_hp"]["condition"] = "asleep"
    del data["difficulty"]["hard"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RulesetError) as error:
        load_ruleset(path)

    assert [(issue.path, issue.code) for issue in error.value.issues] == [
        ("difficulty.hard", "missing_difficulty"),
        ("combat.zero_hp.condition", "unknown_condition"),
    ]


def test_malformed_ruleset_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps({"id": "x", "version": 1}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_ruleset(path)


# --- Шаблон карточки и набор правил подходят друг к другу ---


def test_builtin_template_fits_the_builtin_ruleset(
    srd5_template: CompiledTemplate, srd5_ruleset: Ruleset
) -> None:
    assert srd5_template.template.ruleset == srd5_ruleset.ref
    assert find_compatibility_issues(srd5_template.template, srd5_ruleset) == []


def test_template_resources_are_known_to_the_ruleset(
    srd5_template: CompiledTemplate, srd5_ruleset: Ruleset
) -> None:
    # Ресурсы шаблона связаны с правилами восстановления по id.
    resources = {field.id for field in srd5_template.template.fields if field.type == "resource"}

    assert resources == set(srd5_ruleset.resources)


def test_attack_damage_types_of_pregenerated_characters_are_known(
    srd5_ruleset: Ruleset, fighter: Any, rogue: Any, cleric: Any
) -> None:
    used = {
        attack.damage_type
        for actor in (fighter, rogue, cleric)
        for attack in actor.attacks.values()
    }

    assert used <= set(srd5_ruleset.damage_types)
    assert used == {"slashing", "piercing", "bludgeoning"}


def test_mini_template_does_not_fit_the_5e_ruleset(
    template_data: dict[str, Any], srd5_ruleset: Ruleset
) -> None:
    template_data["ruleset"] = "mini-rules@1"
    template_data["bindings"]["checks"]["lockpicking"] = {"label": "Взлом", "bonus": "dex_mod"}
    del template_data["bindings"]["ac"]
    template = SheetTemplate.model_validate(template_data)

    issues = find_compatibility_issues(template, srd5_ruleset)

    assert issues[0] == SheetIssue(
        "ruleset_mismatch", "ruleset", {"expected": "mini-rules@1", "actual": "dnd5e-srd@1"}
    )
    # Из проверок набора в шаблоне привязана только Скрытность — остальных 23 не хватает.
    missing_checks = [issue.params["check"] for issue in issues if issue.code == "missing_check"]
    assert len(missing_checks) == 23
    assert "stealth" not in missing_checks
    assert SheetIssue("unknown_check", "bindings.checks.lockpicking") in issues
    missing_saves = {issue.params["save"] for issue in issues if issue.code == "missing_save"}
    assert missing_saves == ABILITIES - {"dex"}
    assert SheetIssue("missing_binding", "bindings.ac") in issues
    assert SheetIssue("missing_binding", "bindings.hp") not in issues


def test_skill_may_rely_on_its_ability_binding(
    template_data: dict[str, Any], srd5_ruleset: Ruleset
) -> None:
    # Привязана Ловкость — значит, навыки Ловкости считаются по ней и отдельно не нужны.
    template_data["bindings"]["checks"] = {"dex": {"label": "Ловкость", "bonus": "dex_mod"}}
    template = SheetTemplate.model_validate(template_data)

    missing = {
        issue.params["check"]
        for issue in find_compatibility_issues(template, srd5_ruleset)
        if issue.code == "missing_check"
    }

    assert {"stealth", "acrobatics", "sleight_of_hand", "dex"}.isdisjoint(missing)
    assert {"str", "athletics", "perception"} <= missing
