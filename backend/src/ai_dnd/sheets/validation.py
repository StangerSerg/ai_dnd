"""Проверка шаблона карточки: ссылки, типы, формулы.

Здесь проверки, которым хватает самого документа. Проверки, для которых шаблон
нужно «запустить» (посчитать карточку нового персонажа), — в ``ai_dnd.sheets.compiled``.
"""

import keyword
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from ai_dnd.sheets.formulas import (
    FUNCTIONS,
    FormulaIssue,
    FormulaSyntaxError,
    find_issues,
    parse_formula,
)
from ai_dnd.sheets.issues import SheetIssue
from ai_dnd.sheets.schemas import (
    CheckboxField,
    ComputedField,
    ImageField,
    ListField,
    LongTextField,
    NumberField,
    ResourceField,
    SelectField,
    SheetField,
    SheetTemplate,
    TextField,
)


def resource_max_name(field_id: str) -> str:
    """Имя, под которым максимум ресурса виден в формулах: ``hp`` → ``hp_max``."""
    return f"{field_id}_max"


@dataclass(frozen=True, slots=True)
class FormulaScope:
    """Что видят формулы шаблона."""

    # Имена, значения которых приходят из персонажа.
    inputs: frozenset[str]
    # Имена, которые считаются формулами: вычисляемые поля и максимумы ресурсов.
    formulas: Mapping[str, str]
    # Где в шаблоне записана формула каждого такого имени.
    paths: Mapping[str, str]
    # Поля, на которые формулам ссылаться нельзя (списки, изображения), и их типы.
    non_scalar: Mapping[str, str]


def describe_scope(template: SheetTemplate) -> FormulaScope:
    inputs: set[str] = set()
    formulas: dict[str, str] = {}
    paths: dict[str, str] = {}
    non_scalar: dict[str, str] = {}

    for field in template.fields:
        match field:
            case ComputedField():
                formulas[field.id] = field.formula
                paths[field.id] = f"fields.{field.id}.formula"
            case ResourceField():
                inputs.add(field.id)
                max_name = resource_max_name(field.id)
                if field.max_formula is None:
                    inputs.add(max_name)
                else:
                    formulas[max_name] = field.max_formula
                    paths[max_name] = f"fields.{field.id}.max_formula"
            case ListField() | ImageField():
                non_scalar[field.id] = field.type
            case NumberField() | TextField() | LongTextField() | SelectField() | CheckboxField():
                inputs.add(field.id)

    return FormulaScope(
        inputs=frozenset(inputs - formulas.keys()),
        formulas=formulas,
        paths=paths,
        non_scalar=non_scalar,
    )


def binding_formulas(template: SheetTemplate) -> Iterator[tuple[str, str]]:
    """Формулы из привязок: (место в шаблоне, формула)."""
    bindings = template.bindings
    for check_id, check in bindings.checks.items():
        yield f"bindings.checks.{check_id}.bonus", check.bonus
    for save_id, save in bindings.saves.items():
        yield f"bindings.saves.{save_id}.bonus", save.bonus
    if bindings.ac is not None:
        yield "bindings.ac", bindings.ac
    if bindings.initiative is not None:
        yield "bindings.initiative", bindings.initiative


def find_static_issues(template: SheetTemplate) -> list[SheetIssue]:
    """Все проблемы шаблона, которые видны без вычислений."""
    issues: list[SheetIssue] = []
    fields: dict[str, SheetField] = {}

    for field in template.fields:
        path = f"fields.{field.id}"
        if field.id in fields:
            issues.append(SheetIssue("duplicate_field", path))
            continue
        fields[field.id] = field
        if field.id in FUNCTIONS or keyword.iskeyword(field.id):
            # Такое поле нельзя было бы упомянуть в формуле.
            issues.append(SheetIssue("reserved_name", path))
        issues.extend(_field_issues(field, path))

    for field in fields.values():
        if isinstance(field, ResourceField) and resource_max_name(field.id) in fields:
            issues.append(
                SheetIssue(
                    "name_conflict", f"fields.{field.id}", {"name": resource_max_name(field.id)}
                )
            )

    issues.extend(_layout_issues(template, fields))
    issues.extend(_formula_issues(template))

    hp = template.bindings.hp
    if hp is not None:
        if hp not in fields:
            issues.append(SheetIssue("unknown_field", "bindings.hp", {"field": hp}))
        elif not isinstance(fields[hp], ResourceField):
            issues.append(
                SheetIssue("not_a_resource", "bindings.hp", {"field": hp, "type": fields[hp].type})
            )
    issues.extend(_attacks_binding_issues(template, fields))
    return issues


def _attacks_binding_issues(
    template: SheetTemplate, fields: Mapping[str, SheetField]
) -> Iterator[SheetIssue]:
    """Привязка атак должна указывать на список с колонками нужных типов."""
    binding = template.bindings.attacks
    if binding is None:
        return
    field = fields.get(binding.field)
    if field is None:
        yield SheetIssue("unknown_field", "bindings.attacks.field", {"field": binding.field})
        return
    if not isinstance(field, ListField):
        yield SheetIssue(
            "not_a_list", "bindings.attacks.field", {"field": binding.field, "type": field.type}
        )
        return

    columns = {
        "name": (binding.name, "text"),
        "bonus": (binding.bonus, "number"),
        "damage": (binding.damage, "text"),
        "damage_type": (binding.damage_type, "text"),
    }
    for key, (column_id, expected) in columns.items():
        if column_id is None:
            continue
        path = f"bindings.attacks.{key}"
        column = field.item.get(column_id)
        if column is None:
            yield SheetIssue("unknown_column", path, {"column": column_id})
        elif column.type != expected:
            yield SheetIssue(
                "wrong_column_type",
                path,
                {"column": column_id, "type": column.type, "expected": expected},
            )


def _field_issues(field: SheetField, path: str) -> Iterator[SheetIssue]:
    match field:
        case NumberField():
            yield from _number_field_issues(field, path)
        case SelectField():
            values = [option.value for option in field.options]
            for value in sorted({value for value in values if values.count(value) > 1}):
                yield SheetIssue("duplicate_option", f"{path}.options", {"value": value})
            if field.default is not None and field.default not in values:
                yield SheetIssue("unknown_option", f"{path}.default", {"value": field.default})
        case _:
            return


def _number_field_issues(field: NumberField, path: str) -> Iterator[SheetIssue]:
    if field.integer:
        for name in ("default", "min", "max"):
            if isinstance(getattr(field, name), float):
                yield SheetIssue("not_integer", f"{path}.{name}", {"value": getattr(field, name)})
    if field.min is not None and field.max is not None and field.min > field.max:
        yield SheetIssue("invalid_range", path, {"min": field.min, "max": field.max})
        return
    below = field.min is not None and field.default < field.min
    above = field.max is not None and field.default > field.max
    if below or above:
        yield SheetIssue(
            "out_of_range",
            f"{path}.default",
            {"value": field.default, "min": field.min, "max": field.max},
        )


def _layout_issues(
    template: SheetTemplate, fields: Mapping[str, SheetField]
) -> Iterator[SheetIssue]:
    shown: set[str] = set()
    for section_index, section in enumerate(template.layout):
        for item_index, field_id in enumerate(section.items):
            path = f"layout[{section_index}].items[{item_index}]"
            if field_id not in fields:
                yield SheetIssue("unknown_field", path, {"field": field_id})
            elif field_id in shown:
                yield SheetIssue("duplicate_layout_item", path, {"field": field_id})
            shown.add(field_id)


def _formula_issues(template: SheetTemplate) -> Iterator[SheetIssue]:
    scope = describe_scope(template)

    for issue in find_issues(scope.formulas, scope.inputs):
        yield _from_formula_issue(issue, scope)

    known = scope.inputs | scope.formulas.keys()
    for path, source in binding_formulas(template):
        try:
            formula = parse_formula(source)
        except FormulaSyntaxError as error:
            yield SheetIssue(error.code, path, dict(error.params))
            continue
        for name in sorted(formula.names - known):
            yield _unknown_reference(name, path, scope)


def _from_formula_issue(issue: FormulaIssue, scope: FormulaScope) -> SheetIssue:
    path = scope.paths[issue.field]
    if issue.code == "unknown_reference":
        return _unknown_reference(str(issue.params["name"]), path, scope)
    return SheetIssue(issue.code, path, issue.params)


def _unknown_reference(name: str, path: str, scope: FormulaScope) -> SheetIssue:
    if name in scope.non_scalar:
        # Поле есть, но это список или картинка: числа из него не получить.
        return SheetIssue(
            "non_scalar_reference", path, {"name": name, "type": scope.non_scalar[name]}
        )
    return SheetIssue("unknown_reference", path, {"name": name})
