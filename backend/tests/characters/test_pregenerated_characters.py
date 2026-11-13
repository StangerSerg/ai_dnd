"""Готовые персонажи из content/characters: воин, плут, жрец 3-го уровня.

Ожидаемые числа посчитаны вручную по правилам SRD 5.1.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_dnd.characters.schemas import Character
from ai_dnd.characters.service import TemplateMismatchError, build_sheet, load_character
from ai_dnd.sheets.compiled import CompiledTemplate, ComputedSheet, ResourceState
from ai_dnd.sheets.issues import SheetIssue, SheetValuesError

LANGUAGES = ["ru", "en"]
NAMES = ["fighter", "rogue", "cleric"]

ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]

# Навык → характеристика, от которой он считается.
SKILLS = {
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

EXPECTED: dict[str, dict[str, Any]] = {
    "fighter": {
        # Сила 16, Ловкость 13, Телосложение 15, Интеллект 10, Мудрость 12, Харизма 8.
        "modifiers": {"str": 3, "dex": 1, "con": 2, "int": 0, "wis": 1, "cha": -1},
        "saves": {"str": 5, "dex": 1, "con": 4, "int": 0, "wis": 1, "cha": -1},
        "proficient": {"athletics", "intimidation", "perception", "survival"},
        "expertise": set(),
        # к10: 10 + 2 на первом уровне и по 6 + 2 за второй и третий.
        "hp": 28,
        # Кольчуга 16 + щит 2 + боевой стиль «Оборона» 1; Ловкость к тяжёлому доспеху не идёт.
        "ac": 19,
        "initiative": 1,
        "passive_perception": 13,
        "spell_save_dc": 10,
        "spell_attack": 2,
    },
    "rogue": {
        # Сила 8, Ловкость 17, Телосложение 12, Интеллект 13, Мудрость 12, Харизма 14.
        "modifiers": {"str": -1, "dex": 3, "con": 1, "int": 1, "wis": 1, "cha": 2},
        "saves": {"str": -1, "dex": 5, "con": 1, "int": 3, "wis": 1, "cha": 2},
        "proficient": {"acrobatics", "deception", "investigation", "perception"},
        "expertise": {"sleight_of_hand", "stealth"},
        # к8: 8 + 1 и два раза по 5 + 1.
        "hp": 21,
        # Кожаный доспех 11 + Ловкость 3.
        "ac": 14,
        "initiative": 3,
        "passive_perception": 13,
        "spell_save_dc": 10,
        "spell_attack": 2,
    },
    "cleric": {
        # Сила 14, Ловкость 10, Телосложение 14, Интеллект 10, Мудрость 16, Харизма 12.
        "modifiers": {"str": 2, "dex": 0, "con": 2, "int": 0, "wis": 3, "cha": 1},
        "saves": {"str": 2, "dex": 0, "con": 2, "int": 0, "wis": 5, "cha": 3},
        "proficient": {"insight", "medicine", "persuasion", "religion"},
        "expertise": set(),
        # к8: 8 + 2 и два раза по 5 + 2, плюс 3 за дварфийскую выдержку.
        "hp": 27,
        # Чешуйчатый доспех 14 + Ловкость 0 + щит 2.
        "ac": 16,
        "initiative": 0,
        "passive_perception": 13,
        # 8 + бонус мастерства 2 + Мудрость 3.
        "spell_save_dc": 13,
        "spell_attack": 5,
    },
}

PROFICIENCY_BONUS = 2


def character_path(content_dir: Path, language: str, name: str) -> Path:
    return content_dir / "characters" / language / f"{name}.json"


def sheet_of(
    content_dir: Path, template: CompiledTemplate, language: str, name: str
) -> ComputedSheet:
    return build_sheet(load_character(character_path(content_dir, language, name)), template)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("name", NAMES)
def test_character_is_loaded_and_computed(
    content_dir: Path, srd5_template: CompiledTemplate, language: str, name: str
) -> None:
    expected = EXPECTED[name]
    sheet = sheet_of(content_dir, srd5_template, language, name)

    assert sheet.values["level"] == 3
    assert sheet.computed["prof"] == PROFICIENCY_BONUS
    assert {ability: sheet.computed[f"{ability}_mod"] for ability in ABILITIES} == expected[
        "modifiers"
    ]
    assert sheet.saves == expected["saves"]
    assert sheet.hp == ResourceState(current=expected["hp"], max=expected["hp"])
    assert sheet.resources["hit_dice"] == ResourceState(current=3, max=3)
    assert sheet.ac == expected["ac"]
    assert sheet.initiative == expected["initiative"]
    assert sheet.computed["passive_perception"] == expected["passive_perception"]
    assert sheet.computed["spell_save_dc"] == expected["spell_save_dc"]
    assert sheet.computed["spell_attack"] == expected["spell_attack"]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("name", NAMES)
def test_check_bonuses(
    content_dir: Path, srd5_template: CompiledTemplate, language: str, name: str
) -> None:
    expected = EXPECTED[name]
    modifiers: dict[str, int] = expected["modifiers"]
    sheet = sheet_of(content_dir, srd5_template, language, name)

    expected_checks = dict(modifiers)
    for skill, ability in SKILLS.items():
        multiplier = (
            2 if skill in expected["expertise"] else 1 if skill in expected["proficient"] else 0
        )
        expected_checks[skill] = modifiers[ability] + PROFICIENCY_BONUS * multiplier

    assert sheet.checks == expected_checks
    # Бонус проверки навыка совпадает с тем, что показано в карточке.
    assert {skill: sheet.computed[skill] for skill in SKILLS} == {
        skill: sheet.checks[skill] for skill in SKILLS
    }


def test_rogue_expertise_doubles_proficiency(
    content_dir: Path, srd5_template: CompiledTemplate
) -> None:
    sheet = sheet_of(content_dir, srd5_template, "ru", "rogue")

    # Ловкость +3: компетентность +4, владение +2, без владения +0.
    assert sheet.checks["stealth"] == 7
    assert sheet.checks["sleight_of_hand"] == 7
    assert sheet.checks["acrobatics"] == 5
    assert sheet.checks["dex"] == 3


def test_cleric_spell_slots(content_dir: Path, srd5_template: CompiledTemplate) -> None:
    sheet = sheet_of(content_dir, srd5_template, "ru", "cleric")

    assert sheet.resources["spell_slots_1"] == ResourceState(current=4, max=4)
    assert sheet.resources["spell_slots_2"] == ResourceState(current=2, max=2)
    assert sheet.resources["spell_slots_3"] == ResourceState(current=0, max=0)
    assert sheet.computed["spell_mod"] == 3
    assert len(sheet.values["spells"]) == 11


@pytest.mark.parametrize("name", NAMES)
def test_languages_differ_only_in_texts(content_dir: Path, name: str) -> None:
    def without_texts(value: Any) -> Any:
        if isinstance(value, str):
            return "<текст>"
        if isinstance(value, list):
            return [without_texts(item) for item in value]
        if isinstance(value, dict):
            return {key: without_texts(item) for key, item in value.items()}
        return value

    russian = load_character(character_path(content_dir, "ru", name))
    english = load_character(character_path(content_dir, "en", name))

    # Значения выбора («proficient», «heavy») — не текст, они должны совпадать буквально.
    options = {
        key: value
        for key, value in russian.values.items()
        if key.endswith("_prof") or key in ("armor_type", "spell_ability")
    }
    assert options == {key: english.values[key] for key in options}
    assert without_texts(russian.values) == without_texts(english.values)
    assert russian.values["name"] != english.values["name"]


def test_level_up_recalculates_the_sheet(
    content_dir: Path, srd5_template: CompiledTemplate
) -> None:
    rogue = load_character(character_path(content_dir, "en", "rogue"))
    values = {**rogue.values, "level": 5, "hp": {"current": 30}, "hit_dice": {"current": 5}}

    sheet = build_sheet(Character(template=rogue.template, values=values), srd5_template)

    assert sheet.computed["prof"] == 3
    # Ловкость +3 и удвоенный бонус мастерства.
    assert sheet.checks["stealth"] == 9
    assert sheet.saves["dex"] == 6
    # 9 на первом уровне и по 6 за каждый следующий.
    assert sheet.hp == ResourceState(current=30, max=33)


def test_values_are_checked_against_the_template(
    content_dir: Path, srd5_template: CompiledTemplate
) -> None:
    fighter = load_character(character_path(content_dir, "ru", "fighter"))
    values = {**fighter.values, "str": 40, "stealth_prof": "master", "hp": {"current": 99}}

    with pytest.raises(SheetValuesError) as error:
        build_sheet(Character(template=fighter.template, values=values), srd5_template)

    assert list(error.value.issues) == [
        SheetIssue("out_of_range", "values.str", {"value": 40, "min": 1, "max": 30}),
        SheetIssue("unknown_option", "values.stealth_prof", {"value": "master"}),
    ]


def test_character_of_another_template_version_is_rejected(srd5_template: CompiledTemplate) -> None:
    character = Character(template="srd5-basic@2", values={})

    with pytest.raises(TemplateMismatchError) as error:
        build_sheet(character, srd5_template)

    assert error.value.params == {"expected": "srd5-basic@2", "actual": "srd5-basic@1"}


def test_empty_character_is_a_valid_first_level_one(srd5_template: CompiledTemplate) -> None:
    sheet = build_sheet(Character(template="srd5-basic@1"), srd5_template)

    assert sheet.values["level"] == 1
    assert sheet.computed["prof"] == 2
    assert set(sheet.checks.values()) == {0}
    assert set(sheet.saves.values()) == {0}
    assert (sheet.ac, sheet.initiative) == (10, 0)
    assert sheet.hp == ResourceState(current=8, max=8)


@pytest.mark.parametrize(
    "data",
    [
        {"values": {}},
        {"template": "srd5-basic", "values": {}},
        {"template": "srd5-basic@1", "values": []},
        {"template": "srd5-basic@1", "values": {}, "owner": "кто-то"},
    ],
)
def test_malformed_character_file_is_rejected(tmp_path: Path, data: dict[str, Any]) -> None:
    path = tmp_path / "character.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_character(path)
