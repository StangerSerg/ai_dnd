from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_dnd.sheets.schemas import (
    ComputedField,
    ListField,
    NumberField,
    ResourceField,
    SelectField,
    SheetTemplate,
    TemplateRef,
)


def fields_by_id(template_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in template_data["fields"]}


def test_template_is_parsed(template_data: dict[str, Any]) -> None:
    template = SheetTemplate.model_validate(template_data)

    assert template.ref == "mini@3"
    assert [field.type for field in template.fields] == [
        "text",
        "number",
        "number",
        "computed",
        "computed",
        "checkbox",
        "select",
        "resource",
        "resource",
        "computed",
        "list",
        "longtext",
        "image",
    ]
    assert template.layout[1].cols == 6
    assert template.bindings.checks["stealth"].bonus == "dex_mod + (prof if stealth_prof else 0)"
    assert template.bindings.hp == "hp"


def test_fields_get_their_own_classes(template_data: dict[str, Any]) -> None:
    fields = {field.id: field for field in SheetTemplate.model_validate(template_data).fields}

    level = fields["level"]
    assert isinstance(level, NumberField)
    assert (level.default, level.min, level.max, level.integer) == (1, 1, 20, True)

    dex_mod = fields["dex_mod"]
    assert isinstance(dex_mod, ComputedField)
    assert dex_mod.formula == "floor((dex - 10) / 2)"

    hp, luck = fields["hp"], fields["luck"]
    assert isinstance(hp, ResourceField)
    assert isinstance(luck, ResourceField)
    assert hp.max_formula == "8 + (level - 1) * 5"
    assert (luck.max_formula, luck.default_max) == (None, 3)


def test_label_is_a_string_or_a_dictionary_by_language(template_data: dict[str, Any]) -> None:
    fields = {field.id: field for field in SheetTemplate.model_validate(template_data).fields}

    assert fields["name"].label == "Имя"
    assert fields["level"].label == {"ru": "Уровень", "en": "Level"}


def test_list_column_has_short_and_full_form(template_data: dict[str, Any]) -> None:
    fields = {field.id: field for field in SheetTemplate.model_validate(template_data).fields}
    inventory = fields["inventory"]

    assert isinstance(inventory, ListField)
    assert {name: column.type for name, column in inventory.item.items()} == {
        "name": "text",
        "qty": "number",
        "magic": "checkbox",
    }
    assert inventory.item["name"].label is None
    assert inventory.item["qty"].label == {"ru": "Кол-во", "en": "Qty"}


def test_select_defaults_to_the_first_option(template_data: dict[str, Any]) -> None:
    fields_by_id(template_data)["armor"]["default"] = "heavy"
    with_default = SheetTemplate.model_validate(template_data)
    del fields_by_id(template_data)["armor"]["default"]
    without_default = SheetTemplate.model_validate(template_data)

    for template, expected in ((with_default, "heavy"), (without_default, "none")):
        armor = next(field for field in template.fields if field.id == "armor")
        assert isinstance(armor, SelectField)
        assert armor.default_value == expected


def test_layout_and_bindings_are_optional(template_data: dict[str, Any]) -> None:
    del template_data["layout"]
    del template_data["bindings"]

    template = SheetTemplate.model_validate(template_data)

    assert template.layout == ()
    assert template.bindings.checks == {}
    assert template.bindings.hp is None


def test_template_survives_json_round_trip(template_data: dict[str, Any]) -> None:
    template = SheetTemplate.model_validate(template_data)

    assert SheetTemplate.model_validate_json(template.model_dump_json()) == template


def test_template_is_immutable(template_data: dict[str, Any]) -> None:
    template = SheetTemplate.model_validate(template_data)

    with pytest.raises(ValidationError, match="frozen"):
        template.version = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_id", "key", "value"),
    [
        # Опечатка в названии ключа не должна молча игнорироваться.
        ("dex", "defualt", 10),
        ("dex", "id", "Dex"),
        ("dex", "id", "1dex"),
        ("dex", "id", "dex-mod"),
        ("dex", "id", "ловкость"),
        ("dex", "id", "x" * 41),
        ("dex", "type", "integer"),
        ("dex", "label", ""),
        ("dex", "label", {}),
        ("dex", "label", {"russian": "Ловкость"}),
        ("dex", "label", {"ru": ""}),
        ("dex", "label", 5),
        # Строгие типы: строка или «да/нет» вместо числа — ошибка, а не тихое преобразование.
        ("dex", "default", "10"),
        ("dex", "default", True),
        ("dex", "min", "1"),
        ("dex", "integer", "yes"),
        ("stealth_prof", "default", 1),
        ("stealth_prof", "default", "true"),
        ("name", "default", 5),
        ("armor", "options", []),
        ("armor", "options", [{"value": "", "label": "Пусто"}]),
        ("armor", "options", [{"value": "none"}]),
        ("luck", "default_max", -1),
        ("luck", "default_max", 2.5),
        ("hp", "max_formula", 8),
        ("dex_mod", "formula", None),
        ("inventory", "item", {}),
        ("inventory", "item", {"name": "image"}),
        ("inventory", "item", {"Name": "text"}),
        ("inventory", "item", {"name": {"type": "text", "width": 3}}),
    ],
)
def test_malformed_field_is_rejected(
    template_data: dict[str, Any], field_id: str, key: str, value: object
) -> None:
    fields_by_id(template_data)[field_id][key] = value

    with pytest.raises(ValidationError):
        SheetTemplate.model_validate(template_data)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", "Mini"),
        ("id", "mini_sheet"),
        ("id", ""),
        ("version", 0),
        ("version", "3"),
        ("version", 1.5),
        ("name", ""),
        ("fields", "none"),
        ("layout", [{"section": "Основное", "cols": 0, "items": []}]),
        ("layout", [{"section": "Основное", "cols": 13, "items": []}]),
        ("layout", [{"cols": 2, "items": []}]),
        ("bindings", {"checks": {"stealth": {"bonus": "dex_mod"}}}),
        ("bindings", {"checks": {"Stealth": {"label": "Скрытность", "bonus": "dex_mod"}}}),
        ("bindings", {"attack": "1d20"}),
        ("author", "кто-то"),
    ],
)
def test_malformed_template_is_rejected(
    template_data: dict[str, Any], key: str, value: object
) -> None:
    template_data[key] = value

    with pytest.raises(ValidationError):
        SheetTemplate.model_validate(template_data)


@pytest.mark.parametrize("ref", ["srd5-basic@1", "mini@12", "a@1"])
def test_template_ref_accepts_id_and_version(ref: str) -> None:
    assert TypeAdapter(TemplateRef).validate_python(ref) == ref


@pytest.mark.parametrize("ref", ["srd5-basic", "srd5-basic@", "srd5-basic@0", "Mini@1", "@1", ""])
def test_template_ref_rejects_malformed_values(ref: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TemplateRef).validate_python(ref)
