from typing import Any

import pytest
from pydantic import ValidationError

from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.ruleset import (
    Difficulty,
    EffectTarget,
    Outcome,
    RollBands,
    RollVsTarget,
    Ruleset,
    RulesetError,
    RulesetIssue,
    find_ruleset_issues,
)


@pytest.fixture
def ruleset_data() -> dict[str, Any]:
    """Небольшой правильный набор правил. Тесты портят его по одному месту."""
    return {
        "id": "mini",
        "version": 2,
        "name": {"ru": "Мини", "en": "Mini"},
        "resolution": {"type": "roll_vs_target", "dice": "1d20"},
        "difficulty": {
            "very_easy": 5,
            "easy": 10,
            "medium": 15,
            "hard": 20,
            "very_hard": 25,
            "nearly_impossible": 30,
        },
        "checks": {
            "dex": {"label": "Ловкость"},
            "stealth": {"label": {"ru": "Скрытность", "en": "Stealth"}, "ability": "dex"},
        },
        "saves": {"dex": {"label": "Спасбросок Ловкости", "ability": "dex"}},
        "damage_types": {"fire": "Огонь"},
        "resources": {
            "hp": {"label": "Хиты", "recovery": {"rest": "long"}},
            "luck": {"label": "Удача"},
        },
        "conditions": {
            "poisoned": {
                "label": "Отравлен",
                "effects": [{"applies_to": "checks", "mode": "disadvantage"}],
            },
            "stuck": {
                "label": "Застрял",
                "effects": [{"applies_to": "saves", "only": ["dex"], "auto_fail": True}],
                "prevents_actions": True,
            },
        },
        "combat": {"critical_hit": 20, "automatic_miss": 1, "zero_hp": {"condition": "stuck"}},
    }


def issues_of(ruleset_data: dict[str, Any]) -> list[RulesetIssue]:
    return find_ruleset_issues(Ruleset.model_validate(ruleset_data))


def test_ruleset_is_parsed(ruleset_data: dict[str, Any]) -> None:
    ruleset = Ruleset.model_validate(ruleset_data)

    assert ruleset.ref == "mini@2"
    assert isinstance(ruleset.resolution, RollVsTarget)
    assert ruleset.resolution.dice == "1d20"
    assert ruleset.difficulty[Difficulty.HARD] == 20
    assert ruleset.checks["stealth"].ability == "dex"
    assert ruleset.resources["hp"].recovery is not None
    assert ruleset.resources["hp"].recovery.amount == "full"
    assert ruleset.resources["luck"].recovery is None

    effect = ruleset.conditions["poisoned"].effects[0]
    assert effect.applies_to is EffectTarget.CHECKS
    assert effect.mode is RollMode.DISADVANTAGE
    assert ruleset.conditions["stuck"].prevents_actions is True

    assert ruleset.combat.enabled is True
    assert ruleset.combat.attack_dice == "1d20"
    assert ruleset.combat.critical_damage == "double_dice"
    assert ruleset.combat.zero_hp.creatures_die is True
    assert find_ruleset_issues(ruleset) == []


def test_resolution_by_bands_is_parsed(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["resolution"] = {
        "type": "roll_bands",
        "dice": "2d6",
        "bands": [{"min": 7, "outcome": "partial_success"}, {"min": 10, "outcome": "success"}],
    }

    resolution = Ruleset.model_validate(ruleset_data).resolution

    assert isinstance(resolution, RollBands)
    assert [(band.min, band.outcome) for band in resolution.bands] == [
        (7, Outcome.PARTIAL_SUCCESS),
        (10, Outcome.SUCCESS),
    ]
    assert resolution.below is Outcome.FAILURE


def test_ruleset_survives_json_round_trip(ruleset_data: dict[str, Any]) -> None:
    ruleset = Ruleset.model_validate(ruleset_data)

    assert Ruleset.model_validate_json(ruleset.model_dump_json()) == ruleset


def test_outcomes_know_whether_they_are_successes() -> None:
    assert [outcome for outcome in Outcome if outcome.succeeded] == [
        Outcome.PARTIAL_SUCCESS,
        Outcome.SUCCESS,
        Outcome.CRITICAL_SUCCESS,
    ]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("id",), "Mini"),
        (("version",), 0),
        (("version",), "2"),
        (("name",), ""),
        (("resolution", "type"), "coin_flip"),
        (("resolution", "dice"), "1d"),
        (("resolution", "dice"), "0d20"),
        (("resolution", "critical_success"), "20"),
        (("difficulty", "hard"), "twenty"),
        (("difficulty", "impossible"), 40),
        (("checks", "stealth", "ability"), "Dex"),
        (("checks", "Stealth"), {"label": "Скрытность"}),
        (("checks", "stealth", "bonus"), 2),
        (("resources", "hp", "recovery", "rest"), "nap"),
        (("resources", "hp", "recovery", "amount"), "third"),
        (("conditions", "poisoned", "effects"), [{"applies_to": "damage"}]),
        (("conditions", "poisoned", "effects"), [{"applies_to": "checks", "mode": "normal"}]),
        (("conditions", "poisoned", "effects"), [{"applies_to": "checks", "bonus": 1.5}]),
        (("combat", "attack_dice"), "d"),
        (("combat", "critical_damage"), "triple"),
        (("combat", "surprise"), True),
        (("author",), "кто-то"),
    ],
)
def test_malformed_ruleset_is_rejected(
    ruleset_data: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    place = ruleset_data
    for key in path[:-1]:
        place = place[key]
    place[path[-1]] = value

    with pytest.raises(ValidationError):
        Ruleset.model_validate(ruleset_data)


def test_bands_are_required_for_resolution_by_bands(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["resolution"] = {"type": "roll_bands", "dice": "2d6", "bands": []}

    with pytest.raises(ValidationError):
        Ruleset.model_validate(ruleset_data)


# --- Связи внутри набора ---


def test_every_difficulty_level_needs_a_number(ruleset_data: dict[str, Any]) -> None:
    del ruleset_data["difficulty"]["hard"]
    del ruleset_data["difficulty"]["very_easy"]

    assert issues_of(ruleset_data) == [
        RulesetIssue("missing_difficulty", "difficulty.very_easy"),
        RulesetIssue("missing_difficulty", "difficulty.hard"),
    ]


def test_bands_must_not_repeat(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["resolution"] = {
        "type": "roll_bands",
        "bands": [
            {"min": 7, "outcome": "partial_success"},
            {"min": 10, "outcome": "success"},
            {"min": 7, "outcome": "success"},
        ],
    }

    assert issues_of(ruleset_data) == [
        RulesetIssue("duplicate_band", "resolution.bands", {"min": 7})
    ]


def test_skill_must_point_to_an_existing_base_check(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["checks"]["athletics"] = {"label": "Атлетика", "ability": "str"}
    ruleset_data["checks"]["sneaking"] = {"label": "Подкрадывание", "ability": "stealth"}
    ruleset_data["checks"]["loop"] = {"label": "Петля", "ability": "loop"}
    ruleset_data["saves"]["con"] = {"label": "Спасбросок Телосложения", "ability": "con"}

    assert issues_of(ruleset_data) == [
        RulesetIssue("unknown_check", "checks.athletics.ability", {"check": "str"}),
        RulesetIssue("not_a_base_check", "checks.sneaking.ability", {"check": "stealth"}),
        RulesetIssue("not_a_base_check", "checks.loop.ability", {"check": "loop"}),
        RulesetIssue("unknown_check", "saves.con.ability", {"check": "con"}),
    ]


def test_condition_effects_are_checked(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["conditions"]["odd"] = {
        "label": "Странное",
        "effects": [
            {"applies_to": "checks"},
            {"applies_to": "checks", "only": ["swimming"], "bonus": -2},
            {"applies_to": "saves", "only": ["dex", "wis"], "mode": "advantage"},
            {"applies_to": "attacks", "auto_fail": True},
            {"applies_to": "attacks_against", "only": ["dex"], "mode": "advantage"},
        ],
    }

    assert issues_of(ruleset_data) == [
        RulesetIssue("empty_effect", "conditions.odd.effects[0]"),
        RulesetIssue("unknown_check", "conditions.odd.effects[1].only", {"id": "swimming"}),
        RulesetIssue("unknown_save", "conditions.odd.effects[2].only", {"id": "wis"}),
        RulesetIssue("auto_fail_on_attack", "conditions.odd.effects[3]"),
        RulesetIssue("only_on_attack", "conditions.odd.effects[4].only"),
    ]


def test_zero_hit_points_condition_must_exist(ruleset_data: dict[str, Any]) -> None:
    ruleset_data["combat"]["zero_hp"]["condition"] = "unconscious"

    assert issues_of(ruleset_data) == [
        RulesetIssue("unknown_condition", "combat.zero_hp.condition", {"condition": "unconscious"})
    ]


def test_engine_refuses_a_broken_ruleset(ruleset_data: dict[str, Any]) -> None:
    del ruleset_data["difficulty"]["medium"]
    ruleset_data["combat"]["zero_hp"]["condition"] = "unconscious"

    with pytest.raises(RulesetError) as error:
        RulesEngine(Ruleset.model_validate(ruleset_data))

    assert [(issue.path, issue.code) for issue in error.value.issues] == [
        ("difficulty.medium", "missing_difficulty"),
        ("combat.zero_hp.condition", "unknown_condition"),
    ]
    assert error.value.code == "invalid_ruleset"
    assert "difficulty.medium: missing_difficulty" in str(error.value)
