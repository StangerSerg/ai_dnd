from typing import Any

import pytest

from ai_dnd.sheets.compiled import compile_template, find_template_issues
from ai_dnd.sheets.issues import SheetIssue, TemplateError
from ai_dnd.sheets.schemas import SheetTemplate


def issues_of(template_data: dict[str, Any]) -> list[SheetIssue]:
    return find_template_issues(SheetTemplate.model_validate(template_data))


def fields_by_id(template_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in template_data["fields"]}


def add_computed(template_data: dict[str, Any], field_id: str, formula: str) -> None:
    template_data["fields"].append(
        {"id": field_id, "type": "computed", "label": field_id, "formula": formula}
    )


def test_valid_template_has_no_issues(template_data: dict[str, Any]) -> None:
    assert issues_of(template_data) == []
    assert compile_template(SheetTemplate.model_validate(template_data)).ref == "mini@3"


# --- Поля ---


def test_duplicate_field_id(template_data: dict[str, Any]) -> None:
    template_data["fields"].append({"id": "dex", "type": "text", "label": "Ещё одна ловкость"})

    assert issues_of(template_data) == [SheetIssue("duplicate_field", "fields.dex")]


@pytest.mark.parametrize("field_id", ["max", "floor", "class", "if", "not"])
def test_field_id_must_be_usable_in_formulas(template_data: dict[str, Any], field_id: str) -> None:
    template_data["fields"].append({"id": field_id, "type": "number", "label": "Число"})

    assert issues_of(template_data) == [SheetIssue("reserved_name", f"fields.{field_id}")]


def test_field_id_cannot_shadow_resource_maximum(template_data: dict[str, Any]) -> None:
    # В формулах hp_max — это максимум ресурса hp, отдельное поле с таким id его бы заслонило.
    template_data["fields"].append({"id": "hp_max", "type": "number", "label": "Максимум хитов"})

    assert SheetIssue("name_conflict", "fields.hp", {"name": "hp_max"}) in issues_of(template_data)


def test_integer_field_cannot_have_fractional_settings(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["dex"].update(default=10.5, min=0.5)

    assert issues_of(template_data) == [
        SheetIssue("not_integer", "fields.dex.default", {"value": 10.5}),
        SheetIssue("not_integer", "fields.dex.min", {"value": 0.5}),
    ]

    fields_by_id(template_data)["dex"]["integer"] = False
    assert issues_of(template_data) == []


def test_number_range_must_not_be_inverted(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["dex"].update(min=30, max=1)

    assert issues_of(template_data) == [
        SheetIssue("invalid_range", "fields.dex", {"min": 30, "max": 1})
    ]


@pytest.mark.parametrize("default", [0, 31])
def test_number_default_must_be_within_range(template_data: dict[str, Any], default: int) -> None:
    fields_by_id(template_data)["dex"]["default"] = default

    assert issues_of(template_data) == [
        SheetIssue("out_of_range", "fields.dex.default", {"value": default, "min": 1, "max": 30})
    ]


def test_select_options_must_be_unique(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["armor"]["options"].append({"value": "heavy", "label": "Латы"})

    assert issues_of(template_data) == [
        SheetIssue("duplicate_option", "fields.armor.options", {"value": "heavy"})
    ]


def test_select_default_must_be_an_option(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["armor"]["default"] = "medium"

    assert issues_of(template_data) == [
        SheetIssue("unknown_option", "fields.armor.default", {"value": "medium"})
    ]


# --- Раскладка ---


def test_layout_must_reference_existing_fields(template_data: dict[str, Any]) -> None:
    template_data["layout"][1]["items"].append("strength")

    assert issues_of(template_data) == [
        SheetIssue("unknown_field", "layout[1].items[3]", {"field": "strength"})
    ]


def test_field_is_shown_only_once(template_data: dict[str, Any]) -> None:
    template_data["layout"][1]["items"].append("level")

    assert issues_of(template_data) == [
        SheetIssue("duplicate_layout_item", "layout[1].items[3]", {"field": "level"})
    ]


# --- Формулы ---


def test_broken_formula_points_to_its_place(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["dex_mod"]["formula"] = "floor((dex - 10) / 2"
    fields_by_id(template_data)["hp"]["max_formula"] = "level.real"
    template_data["bindings"]["ac"] = "__import__('os')"

    assert [(issue.path, issue.code) for issue in issues_of(template_data)] == [
        ("fields.dex_mod.formula", "syntax_error"),
        ("fields.hp.max_formula", "forbidden_syntax"),
        ("bindings.ac", "unknown_function"),
    ]


def test_formula_must_reference_existing_fields(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["dex_mod"]["formula"] = "floor((dexterity - 10) / 2)"
    template_data["bindings"]["checks"]["stealth"]["bonus"] = "dex_mod + sneaky"
    template_data["bindings"]["saves"]["dex"]["bonus"] = "dex_save"

    assert issues_of(template_data) == [
        SheetIssue("unknown_reference", "fields.dex_mod.formula", {"name": "dexterity"}),
        SheetIssue("unknown_reference", "bindings.checks.stealth.bonus", {"name": "sneaky"}),
        SheetIssue("unknown_reference", "bindings.saves.dex.bonus", {"name": "dex_save"}),
    ]


@pytest.mark.parametrize(("name", "kind"), [("inventory", "list"), ("portrait", "image")])
def test_formula_cannot_use_lists_and_images(
    template_data: dict[str, Any], name: str, kind: str
) -> None:
    add_computed(template_data, "load", f"{name} * 2")
    template_data["bindings"]["initiative"] = f"dex_mod + {name}"

    assert issues_of(template_data) == [
        SheetIssue("non_scalar_reference", "fields.load.formula", {"name": name, "type": kind}),
        SheetIssue("non_scalar_reference", "bindings.initiative", {"name": name, "type": kind}),
    ]


def test_formula_cycle_is_found(template_data: dict[str, Any]) -> None:
    add_computed(template_data, "attack", "damage + prof")
    add_computed(template_data, "damage", "attack - 1")

    assert issues_of(template_data) == [
        SheetIssue("cycle", "fields.attack.formula", {"cycle": ["attack", "damage"]})
    ]


def test_cycle_through_resource_maximum_is_found(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["hp"]["max_formula"] = "8 + toughness"
    add_computed(template_data, "toughness", "floor(hp_max / 10)")

    assert issues_of(template_data) == [
        SheetIssue("cycle", "fields.hp.max_formula", {"cycle": ["hp_max", "toughness"]})
    ]


def test_formulas_can_read_current_and_maximum_of_resources(template_data: dict[str, Any]) -> None:
    add_computed(template_data, "reserves", "hp + luck + hp_max + luck_max")

    assert issues_of(template_data) == []


def test_maximum_cannot_depend_on_current_value(template_data: dict[str, Any]) -> None:
    # Новый персонаж получает «текущее = максимум», значит максимум должен быть известен раньше.
    add_computed(template_data, "vigor", "luck * 2")
    fields_by_id(template_data)["hp"]["max_formula"] = "8 + vigor + hp"

    assert issues_of(template_data) == [
        SheetIssue("max_reads_resource", "fields.hp.max_formula", {"name": "hp"}),
        SheetIssue("max_reads_resource", "fields.hp.max_formula", {"name": "luck"}),
    ]


# --- Привязки ---


def test_hp_binding_must_point_to_a_resource(template_data: dict[str, Any]) -> None:
    template_data["bindings"]["hp"] = "health"
    assert issues_of(template_data) == [
        SheetIssue("unknown_field", "bindings.hp", {"field": "health"})
    ]

    template_data["bindings"]["hp"] = "level"
    assert issues_of(template_data) == [
        SheetIssue("not_a_resource", "bindings.hp", {"field": "level", "type": "number"})
    ]


# --- Карточка нового персонажа должна считаться ---


def test_formula_must_work_on_default_values(template_data: dict[str, Any]) -> None:
    # У нового персонажа уровень 1 — деление на ноль.
    add_computed(template_data, "pace", "10 / (level - 1)")

    assert issues_of(template_data) == [
        SheetIssue("fails_on_defaults", "fields.pace.formula", {"reason": "division_by_zero"})
    ]


def test_formula_types_are_checked_on_default_values(template_data: dict[str, Any]) -> None:
    add_computed(template_data, "title", "name + 1")

    assert issues_of(template_data) == [
        SheetIssue(
            "fails_on_defaults",
            "fields.title.formula",
            {"reason": "type_mismatch", "operator": "+"},
        )
    ]


def test_bindings_must_give_whole_numbers(template_data: dict[str, Any]) -> None:
    template_data["bindings"]["checks"]["stealth"]["bonus"] = "level / 2"
    template_data["bindings"]["initiative"] = "stealth_prof"
    template_data["bindings"]["ac"] = '"высокий"'

    assert issues_of(template_data) == [
        SheetIssue(
            "fails_on_defaults",
            "bindings.checks.stealth.bonus",
            {"reason": "not_integer", "value": 0.5},
        ),
        SheetIssue(
            "fails_on_defaults", "bindings.ac", {"reason": "not_integer", "value": "высокий"}
        ),
        SheetIssue(
            "fails_on_defaults", "bindings.initiative", {"reason": "not_integer", "value": False}
        ),
    ]


@pytest.mark.parametrize(
    ("formula", "value"), [("level - 5", -4), ("level / 2", 0.5), ("level > 0", True)]
)
def test_resource_maximum_must_be_a_non_negative_integer(
    template_data: dict[str, Any], formula: str, value: object
) -> None:
    fields_by_id(template_data)["hp"]["max_formula"] = formula

    assert issues_of(template_data) == [
        SheetIssue(
            "fails_on_defaults",
            "fields.hp.max_formula",
            {"reason": "invalid_resource_max", "value": value},
        )
    ]


# --- Все проблемы разом ---


def test_compile_reports_all_static_issues_at_once(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["dex"]["default"] = 99
    fields_by_id(template_data)["armor"]["default"] = "medium"
    template_data["layout"][0]["items"].append("ghost")
    fields_by_id(template_data)["prof"]["formula"] = "2 +"
    template_data["bindings"]["hp"] = "level"

    with pytest.raises(TemplateError) as error:
        compile_template(SheetTemplate.model_validate(template_data))

    assert [(issue.path, issue.code) for issue in error.value.issues] == [
        ("fields.dex.default", "out_of_range"),
        ("fields.armor.default", "unknown_option"),
        ("layout[0].items[3]", "unknown_field"),
        ("fields.prof.formula", "syntax_error"),
        ("bindings.hp", "not_a_resource"),
    ]
    assert error.value.code == "invalid_template"
    assert "fields.armor.default: unknown_option (value='medium')" in str(error.value)
