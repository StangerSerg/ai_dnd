"""Привязка атак: список в карточке, из которого движок правил берёт атаки персонажа."""

from typing import Any

import pytest

from ai_dnd.sheets.compiled import SheetAttack, compile_template, find_template_issues
from ai_dnd.sheets.issues import SheetIssue, SheetValuesError
from ai_dnd.sheets.schemas import SheetTemplate


@pytest.fixture
def with_attacks(template_data: dict[str, Any]) -> dict[str, Any]:
    """Шаблон-заготовка, в который добавлены список атак и привязка к нему."""
    template_data["fields"].append(
        {
            "id": "attacks",
            "type": "list",
            "label": "Атаки",
            "item": {
                "name": "text",
                "to_hit": "number",
                "damage": "text",
                "kind": "text",
                "silver": "checkbox",
            },
        }
    )
    template_data["bindings"]["attacks"] = {
        "field": "attacks",
        "bonus": "to_hit",
        "damage_type": "kind",
    }
    return template_data


def issues_of(template_data: dict[str, Any]) -> list[SheetIssue]:
    return find_template_issues(SheetTemplate.model_validate(template_data))


def test_attacks_are_read_from_the_list(with_attacks: dict[str, Any]) -> None:
    template = compile_template(SheetTemplate.model_validate(with_attacks))

    sheet = template.compute(
        {
            "attacks": [
                {"name": "Кинжал", "to_hit": 5, "damage": "1d4+3", "kind": "piercing"},
                {"name": "Захват", "to_hit": 2, "silver": True},
            ]
        }
    )

    assert sheet.attacks == (
        SheetAttack(name="Кинжал", bonus=5, damage="1d4+3", damage_type="piercing"),
        # Пустые урон и тип урона — «не указано».
        SheetAttack(name="Захват", bonus=2, damage=None, damage_type=None),
    )
    assert template.compute({}).attacks == ()


def test_column_names_default_to_the_usual_ones(template_data: dict[str, Any]) -> None:
    template_data["fields"].append(
        {
            "id": "attacks",
            "type": "list",
            "label": "Атаки",
            "item": {"name": "text", "bonus": "number", "damage": "text"},
        }
    )
    template_data["bindings"]["attacks"] = {"field": "attacks"}
    template = compile_template(SheetTemplate.model_validate(template_data))

    sheet = template.compute({"attacks": [{"name": "Посох", "bonus": 2, "damage": "1d6"}]})

    assert sheet.attacks == (SheetAttack(name="Посох", bonus=2, damage="1d6"),)


def test_sheet_without_the_binding_has_no_attacks(template_data: dict[str, Any]) -> None:
    template = compile_template(SheetTemplate.model_validate(template_data))

    assert template.compute({}).attacks == ()


def test_attack_bonus_must_be_a_whole_number(with_attacks: dict[str, Any]) -> None:
    template = compile_template(SheetTemplate.model_validate(with_attacks))
    values = {"attacks": [{"name": "Кинжал", "to_hit": 5}, {"name": "Праща", "to_hit": 2.5}]}

    with pytest.raises(SheetValuesError) as error:
        template.compute(values)

    assert list(error.value.issues) == [
        SheetIssue("not_integer", "values.attacks[1].to_hit", {"value": 2.5})
    ]


def test_binding_must_point_to_a_list(with_attacks: dict[str, Any]) -> None:
    with_attacks["bindings"]["attacks"]["field"] = "weapons"
    assert issues_of(with_attacks) == [
        SheetIssue("unknown_field", "bindings.attacks.field", {"field": "weapons"})
    ]

    with_attacks["bindings"]["attacks"]["field"] = "notes"
    assert issues_of(with_attacks) == [
        SheetIssue("not_a_list", "bindings.attacks.field", {"field": "notes", "type": "longtext"})
    ]


def test_binding_columns_must_exist_and_have_the_right_type(
    with_attacks: dict[str, Any],
) -> None:
    with_attacks["bindings"]["attacks"] = {
        "field": "attacks",
        "name": "title",
        "bonus": "damage",
        "damage": "to_hit",
        "damage_type": "silver",
    }

    assert issues_of(with_attacks) == [
        SheetIssue("unknown_column", "bindings.attacks.name", {"column": "title"}),
        SheetIssue(
            "wrong_column_type",
            "bindings.attacks.bonus",
            {"column": "damage", "type": "text", "expected": "number"},
        ),
        SheetIssue(
            "wrong_column_type",
            "bindings.attacks.damage",
            {"column": "to_hit", "type": "number", "expected": "text"},
        ),
        SheetIssue(
            "wrong_column_type",
            "bindings.attacks.damage_type",
            {"column": "silver", "type": "checkbox", "expected": "text"},
        ),
    ]
