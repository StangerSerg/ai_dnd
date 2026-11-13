from typing import Any

import pytest

from ai_dnd.sheets.compiled import (
    MAX_LIST_ITEMS,
    MAX_TEXT_LENGTH,
    CompiledTemplate,
    ComputedSheet,
    ResourceState,
    compile_template,
)
from ai_dnd.sheets.issues import SheetIssue, SheetValuesError
from ai_dnd.sheets.schemas import SheetTemplate


@pytest.fixture
def template(template_data: dict[str, Any]) -> CompiledTemplate:
    return compile_template(SheetTemplate.model_validate(template_data))


def issues_of(template: CompiledTemplate, values: dict[str, Any]) -> list[SheetIssue]:
    with pytest.raises(SheetValuesError) as error:
        template.compute(values)
    return list(error.value.issues)


# --- Расчёт ---


def test_new_character_gets_default_values(template: CompiledTemplate) -> None:
    sheet = template.compute({})

    assert sheet.values == {
        "name": "",
        "level": 1,
        "dex": 10,
        "stealth_prof": False,
        "armor": "none",
        "hp": {"current": 8},
        "luck": {"current": 3, "max": 3},
        "inventory": [],
        "notes": "",
        "portrait": None,
    }
    assert sheet.computed == {"dex_mod": 0, "prof": 2, "bloodied": False}
    assert sheet.resources == {
        "hp": ResourceState(current=8, max=8),
        "luck": ResourceState(current=3, max=3),
    }
    assert sheet.checks == {"stealth": 0}
    assert sheet.saves == {"dex": 0}
    assert (sheet.ac, sheet.initiative, sheet.hp) == (10, 0, ResourceState(current=8, max=8))
    assert template.defaults() == sheet.values


def test_sheet_is_computed_from_values(template: CompiledTemplate) -> None:
    sheet = template.compute(
        {
            "name": "Мира",
            "level": 5,
            "dex": 17,
            "stealth_prof": True,
            "hp": {"current": 20},
            "luck": {"current": 1, "max": 4},
            "inventory": [{"name": "Верёвка", "qty": 2}, {"name": "Кольцо", "magic": True}],
            "portrait": "assets/mira.webp",
        }
    )

    assert sheet.computed == {"dex_mod": 3, "prof": 3, "bloodied": False}
    assert sheet.checks == {"stealth": 6}
    assert sheet.saves == {"dex": 3}
    assert sheet.ac == 13
    assert sheet.initiative == 3
    assert sheet.hp == ResourceState(current=20, max=28)
    assert sheet.resources["luck"] == ResourceState(current=1, max=4)
    # В списке недостающие колонки получают значения по умолчанию.
    assert sheet.values["inventory"] == [
        {"name": "Верёвка", "qty": 2, "magic": False},
        {"name": "Кольцо", "qty": 0, "magic": True},
    ]
    assert sheet.values["portrait"] == "assets/mira.webp"


def test_select_value_is_visible_to_formulas(template: CompiledTemplate) -> None:
    assert template.compute({"dex": 18, "armor": "heavy"}).ac == 16
    assert template.compute({"dex": 18, "armor": "none"}).ac == 14


@pytest.mark.parametrize(("level", "prof"), [(1, 2), (4, 2), (5, 3), (9, 4), (17, 6)])
def test_change_of_one_field_reaches_dependent_ones(
    template: CompiledTemplate, level: int, prof: int
) -> None:
    sheet = template.compute({"level": level, "dex": 14, "stealth_prof": True})

    assert sheet.computed["prof"] == prof
    assert sheet.checks["stealth"] == 2 + prof
    assert sheet.resources["hp"].max == 8 + (level - 1) * 5


def test_input_values_are_not_modified(template: CompiledTemplate) -> None:
    values = {"level": 2, "hp": {}, "inventory": [{"name": "Факел"}]}

    template.compute(values)

    assert values == {"level": 2, "hp": {}, "inventory": [{"name": "Факел"}]}


def test_sheet_survives_json_round_trip(template: CompiledTemplate) -> None:
    sheet = template.compute({"dex": 15, "hp": {"current": 3}, "inventory": [{"name": "Факел"}]})

    assert ComputedSheet.model_validate_json(sheet.model_dump_json()) == sheet


# --- Ресурсы ---


def test_resource_without_current_value_is_full(template: CompiledTemplate) -> None:
    sheet = template.compute({"level": 3, "luck": {"max": 5}})

    assert sheet.resources == {
        "hp": ResourceState(current=18, max=18),
        "luck": ResourceState(current=5, max=5),
    }
    assert sheet.values["hp"] == {"current": 18}
    assert sheet.values["luck"] == {"current": 5, "max": 5}
    # «Ранен» считается уже по заполненным хитам, а не по нулю.
    assert sheet.computed["bloodied"] is False


def test_formulas_see_current_and_maximum_of_a_resource(template: CompiledTemplate) -> None:
    assert template.compute({"level": 3, "hp": {"current": 9}}).computed["bloodied"] is True
    assert template.compute({"level": 3, "hp": {"current": 10}}).computed["bloodied"] is False
    assert template.compute({"level": 3, "hp": {"current": 0}}).hp == ResourceState(
        current=0, max=18
    )


def test_resource_cannot_exceed_its_maximum(template: CompiledTemplate) -> None:
    assert issues_of(template, {"hp": {"current": 9}, "luck": {"current": 4, "max": 3}}) == [
        SheetIssue("above_max", "values.hp.current", {"value": 9, "max": 8}),
        SheetIssue("above_max", "values.luck.current", {"value": 4, "max": 3}),
    ]


def test_computed_maximum_cannot_be_stored(template: CompiledTemplate) -> None:
    assert issues_of(template, {"hp": {"current": 5, "max": 50}}) == [
        SheetIssue("max_is_computed", "values.hp.max")
    ]


@pytest.mark.parametrize(
    ("value", "issue"),
    [
        (5, SheetIssue("wrong_type", "values.luck", {"expected": "resource"})),
        ([1, 3], SheetIssue("wrong_type", "values.luck", {"expected": "resource"})),
        ({"current": "2"}, SheetIssue("wrong_type", "values.luck.current", {"expected": "number"})),
        ({"current": 1.5}, SheetIssue("not_integer", "values.luck.current", {"value": 1.5})),
        (
            {"current": -1},
            SheetIssue(
                "out_of_range", "values.luck.current", {"value": -1, "min": 0, "max": 10**9}
            ),
        ),
        ({"max": True}, SheetIssue("wrong_type", "values.luck.max", {"expected": "number"})),
        ({"current": 1, "temp": 2}, SheetIssue("unknown_key", "values.luck.temp")),
    ],
)
def test_malformed_resource_value(
    template: CompiledTemplate, value: object, issue: SheetIssue
) -> None:
    assert issues_of(template, {"luck": value}) == [issue]


# --- Проверка значений по шаблону ---


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("dex", "16", SheetIssue("wrong_type", "values.dex", {"expected": "number"})),
        ("dex", True, SheetIssue("wrong_type", "values.dex", {"expected": "number"})),
        ("dex", None, SheetIssue("wrong_type", "values.dex", {"expected": "number"})),
        ("dex", float("nan"), SheetIssue("wrong_type", "values.dex", {"expected": "number"})),
        ("dex", 16.5, SheetIssue("not_integer", "values.dex", {"value": 16.5})),
        ("dex", 16.0, SheetIssue("not_integer", "values.dex", {"value": 16.0})),
        ("dex", 31, SheetIssue("out_of_range", "values.dex", {"value": 31, "min": 1, "max": 30})),
        ("level", 0, SheetIssue("out_of_range", "values.level", {"value": 0, "min": 1, "max": 20})),
        ("name", 5, SheetIssue("wrong_type", "values.name", {"expected": "text"})),
        (
            "name",
            "я" * (MAX_TEXT_LENGTH + 1),
            SheetIssue(
                "too_long", "values.name", {"length": MAX_TEXT_LENGTH + 1, "max": MAX_TEXT_LENGTH}
            ),
        ),
        ("notes", ["строка"], SheetIssue("wrong_type", "values.notes", {"expected": "text"})),
        (
            "stealth_prof",
            1,
            SheetIssue("wrong_type", "values.stealth_prof", {"expected": "checkbox"}),
        ),
        (
            "stealth_prof",
            "да",
            SheetIssue("wrong_type", "values.stealth_prof", {"expected": "checkbox"}),
        ),
        ("armor", "medium", SheetIssue("unknown_option", "values.armor", {"value": "medium"})),
        ("armor", 2, SheetIssue("wrong_type", "values.armor", {"expected": "select"})),
        ("portrait", 7, SheetIssue("wrong_type", "values.portrait", {"expected": "text"})),
        (
            "inventory",
            {"name": "Факел"},
            SheetIssue("wrong_type", "values.inventory", {"expected": "list"}),
        ),
        (
            "inventory",
            ["Факел"],
            SheetIssue("wrong_type", "values.inventory[0]", {"expected": "list_item"}),
        ),
        (
            "inventory",
            [{"name": "Факел"}, {"name": "Верёвка", "weight": 5}],
            SheetIssue("unknown_key", "values.inventory[1].weight"),
        ),
        (
            "inventory",
            [{"name": "Факел", "qty": "много"}],
            SheetIssue("wrong_type", "values.inventory[0].qty", {"expected": "number"}),
        ),
        (
            "inventory",
            [{"name": "Факел", "magic": "нет"}],
            SheetIssue("wrong_type", "values.inventory[0].magic", {"expected": "checkbox"}),
        ),
        (
            "inventory",
            [{}] * (MAX_LIST_ITEMS + 1),
            SheetIssue(
                "too_long",
                "values.inventory",
                {"length": MAX_LIST_ITEMS + 1, "max": MAX_LIST_ITEMS},
            ),
        ),
        # Вычисляемое поле хранить нельзя: его значение устареет.
        ("dex_mod", 5, SheetIssue("computed_field", "values.dex_mod")),
        ("strength", 15, SheetIssue("unknown_field", "values.strength")),
    ],
)
def test_value_that_does_not_fit_the_template(
    template: CompiledTemplate, field: str, value: object, issue: SheetIssue
) -> None:
    assert issues_of(template, {field: value}) == [issue]


def test_fractional_number_is_fine_where_template_allows_it(template_data: dict[str, Any]) -> None:
    template_data["fields"].append(
        {"id": "weight", "type": "number", "label": "Вес", "integer": False, "min": 0}
    )
    template = compile_template(SheetTemplate.model_validate(template_data))

    sheet = template.compute({"weight": 72.5, "inventory": [{"name": "Зелье", "qty": 0.5}]})

    assert sheet.values["weight"] == 72.5
    assert sheet.values["inventory"][0]["qty"] == 0.5


def test_all_value_issues_are_reported_at_once(template: CompiledTemplate) -> None:
    issues = issues_of(
        template,
        {"dex": "16", "level": 25, "armor": "medium", "ghost": 1, "prof": 3, "hp": {"max": 9}},
    )

    assert [(issue.path, issue.code) for issue in issues] == [
        ("values.ghost", "unknown_field"),
        ("values.level", "out_of_range"),
        ("values.dex", "wrong_type"),
        ("values.prof", "computed_field"),
        ("values.armor", "unknown_option"),
        ("values.hp.max", "max_is_computed"),
    ]


# --- Формулы, которые ломаются на значениях конкретного персонажа ---


def test_formula_error_points_to_the_formula(template_data: dict[str, Any]) -> None:
    template_data["fields"].append(
        {"id": "pace", "type": "computed", "label": "Темп", "formula": "100 / (dex - 5)"}
    )
    template = compile_template(SheetTemplate.model_validate(template_data))

    assert template.compute({"dex": 15}).computed["pace"] == 10
    assert issues_of(template, {"dex": 5}) == [
        SheetIssue("division_by_zero", "fields.pace.formula")
    ]


def test_binding_must_stay_a_whole_number(template_data: dict[str, Any]) -> None:
    # На уровне 2 (по умолчанию) бонус целый, на нечётных уровнях — уже нет.
    template_data["fields"][1]["default"] = 2
    template_data["bindings"]["checks"]["stealth"]["bonus"] = "level / 2"
    template = compile_template(SheetTemplate.model_validate(template_data))

    assert template.compute({"level": 4}).checks["stealth"] == 2
    assert issues_of(template, {"level": 3}) == [
        SheetIssue("not_integer", "bindings.checks.stealth.bonus", {"value": 1.5})
    ]


def test_resource_maximum_must_stay_valid(template_data: dict[str, Any]) -> None:
    template_data["fields"][7]["max_formula"] = "20 - dex"
    template = compile_template(SheetTemplate.model_validate(template_data))

    assert template.compute({"dex": 20}).hp == ResourceState(current=0, max=0)
    assert issues_of(template, {"dex": 21}) == [
        SheetIssue("invalid_resource_max", "fields.hp.max_formula", {"value": -1})
    ]
