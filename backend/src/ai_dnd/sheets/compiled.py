"""Проверенный шаблон карточки и расчёт карточки персонажа.

    template = compile_template(SheetTemplate.model_validate_json(text))
    sheet = template.compute({"level": 3, "dex": 16})
    sheet.checks["stealth"]   # бонус проверки Скрытности

``compile_template`` находит все проблемы шаблона разом, ``compute`` — все проблемы
значений персонажа: редактору и игроку удобнее увидеть полный список, а не первую ошибку.
"""

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ai_dnd.sheets.formulas import (
    Formula,
    FormulaEvaluationError,
    FormulaGraph,
    Value,
    evaluate,
    parse_formula,
)
from ai_dnd.sheets.issues import SheetIssue, SheetValuesError, TemplateError
from ai_dnd.sheets.schemas import (
    CheckBinding,
    CheckboxField,
    ComputedField,
    ImageField,
    ListColumn,
    ListField,
    LongTextField,
    NumberField,
    ResourceField,
    SelectField,
    SheetTemplate,
    TextField,
)
from ai_dnd.sheets.validation import (
    FormulaScope,
    describe_scope,
    find_static_issues,
    resource_max_name,
)

MAX_TEXT_LENGTH = 200
MAX_LONGTEXT_LENGTH = 10_000
MAX_IMAGE_REF_LENGTH = 500
MAX_LIST_ITEMS = 200
# Числа крупнее в карточке не нужны, а формулы всё равно откажутся их считать.
MAX_NUMBER = 10**9


class ResourceState(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: int
    max: int


class SheetAttack(BaseModel):
    """Атака из карточки — так, как она нужна движку правил."""

    model_config = ConfigDict(frozen=True)

    name: str
    bonus: int
    # Запись урона («1d8+3») и тип урона. None — не указано.
    damage: str | None = None
    damage_type: str | None = None


class ComputedSheet(BaseModel):
    """Карточка персонажа после расчёта."""

    model_config = ConfigDict(frozen=True)

    # Хранимые значения полей: то, что ввёл игрок, плюс значения по умолчанию.
    values: dict[str, Any]
    # Вычисляемые поля.
    computed: dict[str, Value]
    # Ресурсы с уже посчитанным максимумом.
    resources: dict[str, ResourceState]
    # Привязки: бонусы проверок и спасбросков, класс доспеха, бонус инициативы, хиты, атаки.
    checks: dict[str, int]
    saves: dict[str, int]
    ac: int | None = None
    initiative: int | None = None
    hp: ResourceState | None = None
    # Id ресурса, в котором лежат хиты.
    hp_resource: str | None = None
    attacks: tuple[SheetAttack, ...] = ()


class CompiledTemplate:
    """Шаблон, по которому можно считать карточки. Создаётся через ``compile_template``."""

    def __init__(self, template: SheetTemplate) -> None:
        self.template = template
        self._scope: FormulaScope = describe_scope(template)
        self._graph = FormulaGraph(self._scope.formulas, self._scope.inputs)
        bindings = template.bindings
        self._checks = _parse_bindings("checks", bindings.checks)
        self._saves = _parse_bindings("saves", bindings.saves)
        self._ac = None if bindings.ac is None else parse_formula(bindings.ac)
        self._initiative = (
            None if bindings.initiative is None else parse_formula(bindings.initiative)
        )

    @property
    def ref(self) -> str:
        return self.template.ref

    def defaults(self) -> dict[str, Any]:
        """Значения полей нового персонажа."""
        return self.compute({}).values

    def compute(self, values: Mapping[str, Any]) -> ComputedSheet:
        """Проверить значения по шаблону и посчитать карточку.

        Пропущенные поля получают значения по умолчанию, ресурс без текущего значения
        считается полным. Если что-то не так — ``SheetValuesError`` со всеми проблемами.
        """
        stored, issues = self._normalize(values)
        if issues:
            raise SheetValuesError(issues)

        scope = self._formula_inputs(stored)
        computed = self._evaluate_formulas(scope)
        maximums = self._resource_maximums(stored, computed)

        refilled = False
        for field_id, maximum in maximums.items():
            if stored[field_id]["current"] is None:
                stored[field_id]["current"] = scope[field_id] = maximum
                refilled = True
        if refilled:
            # Максимумы от текущих значений не зависят (это проверено при сборке шаблона),
            # а остальные формулы могли — пересчитываем их с заполненными ресурсами.
            computed = self._evaluate_formulas(scope)

        resources: dict[str, ResourceState] = {}
        for field_id, maximum in maximums.items():
            current = stored[field_id]["current"]
            if current > maximum:
                issues.append(
                    SheetIssue(
                        "above_max",
                        f"values.{field_id}.current",
                        {"value": current, "max": maximum},
                    )
                )
            resources[field_id] = ResourceState(current=current, max=maximum)

        full_scope = {**scope, **computed}
        checks = {
            check_id: _integer_binding(formula, full_scope, path, issues)
            for check_id, (path, formula) in self._checks.items()
        }
        saves = {
            save_id: _integer_binding(formula, full_scope, path, issues)
            for save_id, (path, formula) in self._saves.items()
        }
        ac = (
            None
            if self._ac is None
            else _integer_binding(self._ac, full_scope, "bindings.ac", issues)
        )
        initiative = (
            None
            if self._initiative is None
            else _integer_binding(self._initiative, full_scope, "bindings.initiative", issues)
        )
        attacks = self._attacks(stored, issues)
        if issues:
            raise SheetValuesError(issues)

        hp = self.template.bindings.hp
        return ComputedSheet(
            values=stored,
            computed={
                field.id: computed[field.id]
                for field in self.template.fields
                if isinstance(field, ComputedField)
            },
            resources=resources,
            checks=checks,
            saves=saves,
            ac=ac,
            initiative=initiative,
            hp=None if hp is None else resources[hp],
            hp_resource=hp,
            attacks=attacks,
        )

    def _attacks(
        self, stored: Mapping[str, Any], issues: list[SheetIssue]
    ) -> tuple[SheetAttack, ...]:
        binding = self.template.bindings.attacks
        if binding is None:
            return ()
        attacks: list[SheetAttack] = []
        for index, item in enumerate(stored[binding.field]):
            bonus = item[binding.bonus]
            if isinstance(bonus, float):
                # К броску атаки прибавляется целое число.
                path = f"values.{binding.field}[{index}].{binding.bonus}"
                issues.append(SheetIssue("not_integer", path, {"value": bonus}))
                continue
            damage_type = None if binding.damage_type is None else item[binding.damage_type]
            attacks.append(
                SheetAttack(
                    name=item[binding.name],
                    bonus=bonus,
                    damage=item[binding.damage] or None,
                    damage_type=damage_type or None,
                )
            )
        return tuple(attacks)

    # --- Значения персонажа ---

    def _normalize(self, values: Mapping[str, Any]) -> tuple[dict[str, Any], list[SheetIssue]]:
        """Хранимые значения с подставленными значениями по умолчанию и список проблем."""
        issues: list[SheetIssue] = []
        stored: dict[str, Any] = {}

        known = {field.id for field in self.template.fields}
        issues.extend(
            SheetIssue("unknown_field", f"values.{name}") for name in values if name not in known
        )

        for field in self.template.fields:
            path = f"values.{field.id}"
            present = field.id in values
            raw = values.get(field.id)
            match field:
                case ComputedField():
                    if present:
                        # Вычисляемое поле хранить нельзя: его значение устареет.
                        issues.append(SheetIssue("computed_field", path))
                case NumberField():
                    stored[field.id] = (
                        _number(raw, path, issues, field.integer, field.min, field.max)
                        if present
                        else field.default
                    )
                case TextField():
                    stored[field.id] = (
                        _text(raw, path, issues, MAX_TEXT_LENGTH) if present else field.default
                    )
                case LongTextField():
                    stored[field.id] = (
                        _text(raw, path, issues, MAX_LONGTEXT_LENGTH) if present else field.default
                    )
                case SelectField():
                    stored[field.id] = (
                        _option(raw, path, issues, field) if present else field.default_value
                    )
                case CheckboxField():
                    stored[field.id] = _checkbox(raw, path, issues) if present else field.default
                case ResourceField():
                    stored[field.id] = _resource(raw if present else {}, path, issues, field)
                case ListField():
                    stored[field.id] = _list(raw, path, issues, field) if present else []
                case ImageField():
                    stored[field.id] = _image(raw, path, issues) if present else None
        return stored, issues

    def _formula_inputs(self, stored: Mapping[str, Any]) -> dict[str, Value]:
        scope: dict[str, Value] = {}
        for field in self.template.fields:
            match field:
                case ResourceField():
                    # Незаполненный ресурс на первом проходе считаем пустым.
                    scope[field.id] = stored[field.id]["current"] or 0
                    if field.max_formula is None:
                        scope[resource_max_name(field.id)] = stored[field.id]["max"]
                case (
                    NumberField() | TextField() | LongTextField() | SelectField() | CheckboxField()
                ):
                    scope[field.id] = stored[field.id]
                case _:
                    pass
        return scope

    def _evaluate_formulas(self, scope: Mapping[str, Value]) -> dict[str, Value]:
        try:
            return self._graph.evaluate(scope)
        except FormulaEvaluationError as error:
            params = dict(error.params)
            path = self._scope.paths[str(params.pop("field"))]
            raise SheetValuesError([SheetIssue(error.code, path, params)]) from None

    def _resource_maximums(
        self, stored: Mapping[str, Any], computed: Mapping[str, Value]
    ) -> dict[str, int]:
        issues: list[SheetIssue] = []
        maximums: dict[str, int] = {}
        for field in self.template.fields:
            if not isinstance(field, ResourceField):
                continue
            if field.max_formula is None:
                maximums[field.id] = stored[field.id]["max"]
                continue
            value = computed[resource_max_name(field.id)]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append(
                    SheetIssue(
                        "invalid_resource_max", f"fields.{field.id}.max_formula", {"value": value}
                    )
                )
                continue
            maximums[field.id] = value
        if issues:
            raise SheetValuesError(issues)
        return maximums

    # --- Проверки, для которых нужен собранный шаблон ---

    def max_formulas_reading_resources(self) -> list[SheetIssue]:
        """Формулы максимума, которые (прямо или через другие поля) читают текущее
        значение какого-нибудь ресурса.

        Так нельзя: новый персонаж получает «текущее = максимум», и максимум должен быть
        известен раньше текущего значения.
        """
        currents = {field.id for field in self.template.fields if isinstance(field, ResourceField)}
        issues: list[SheetIssue] = []
        for field_id in sorted(currents):
            start = resource_max_name(field_id)
            if start not in self._scope.formulas:
                continue
            seen: set[str] = set()
            stack = [start]
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                if name in self._scope.formulas:
                    stack.extend(self._graph.dependencies(name))
            issues.extend(
                SheetIssue("max_reads_resource", self._scope.paths[start], {"name": name})
                for name in sorted(seen & currents)
            )
        return issues


def compile_template(template: SheetTemplate) -> CompiledTemplate:
    """Проверить шаблон целиком. Если есть проблемы — ``TemplateError`` со всеми сразу."""
    issues = find_static_issues(template)
    if issues:
        raise TemplateError(issues)

    compiled = CompiledTemplate(template)
    issues = compiled.max_formulas_reading_resources()
    if issues:
        raise TemplateError(issues)

    try:
        # Карточка только что созданного персонажа должна считаться без ошибок.
        compiled.compute({})
    except SheetValuesError as error:
        raise TemplateError(
            [
                SheetIssue("fails_on_defaults", issue.path, {"reason": issue.code, **issue.params})
                for issue in error.issues
            ]
        ) from None
    return compiled


def find_template_issues(template: SheetTemplate) -> list[SheetIssue]:
    """Все проблемы шаблона. Пустой список — шаблоном можно пользоваться."""
    try:
        compile_template(template)
    except TemplateError as error:
        return list(error.issues)
    return []


def load_template(path: Path) -> CompiledTemplate:
    """Прочитать шаблон из JSON-файла и проверить его."""
    return compile_template(SheetTemplate.model_validate_json(path.read_text(encoding="utf-8")))


# --- Привязки ---


def _parse_bindings(
    group: str, bindings: Mapping[str, CheckBinding]
) -> dict[str, tuple[str, Formula]]:
    return {
        binding_id: (f"bindings.{group}.{binding_id}.bonus", parse_formula(binding.bonus))
        for binding_id, binding in bindings.items()
    }


def _integer_binding(
    formula: Formula, scope: Mapping[str, Value], path: str, issues: list[SheetIssue]
) -> int:
    """Значение привязки. Движку нужны целые числа: к броску нельзя прибавить 2.5 или «да»."""
    try:
        value = evaluate(formula, scope)
    except FormulaEvaluationError as error:
        issues.append(SheetIssue(error.code, path, dict(error.params)))
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(SheetIssue("not_integer", path, {"value": value}))
        return 0
    return value


# --- Проверка значений по типам полей ---
# Каждая функция возвращает проверенное значение, а при ошибке добавляет проблему
# в список и возвращает заглушку: карточка с проблемами всё равно не считается.


def _number(
    raw: object,
    path: str,
    issues: list[SheetIssue],
    integer: bool,
    minimum: float | None = None,
    maximum: float | None = None,
) -> int | float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        issues.append(SheetIssue("wrong_type", path, {"expected": "number"}))
        return 0
    if integer and isinstance(raw, float):
        issues.append(SheetIssue("not_integer", path, {"value": raw}))
        return 0
    low = -MAX_NUMBER if minimum is None else minimum
    high = MAX_NUMBER if maximum is None else maximum
    if not low <= raw <= high:
        issues.append(SheetIssue("out_of_range", path, {"value": raw, "min": low, "max": high}))
        return 0
    return raw


def _text(raw: object, path: str, issues: list[SheetIssue], max_length: int) -> str:
    if not isinstance(raw, str):
        issues.append(SheetIssue("wrong_type", path, {"expected": "text"}))
        return ""
    if len(raw) > max_length:
        issues.append(SheetIssue("too_long", path, {"length": len(raw), "max": max_length}))
        return ""
    return raw


def _checkbox(raw: object, path: str, issues: list[SheetIssue]) -> bool:
    if not isinstance(raw, bool):
        issues.append(SheetIssue("wrong_type", path, {"expected": "checkbox"}))
        return False
    return raw


def _option(raw: object, path: str, issues: list[SheetIssue], field: SelectField) -> str:
    if not isinstance(raw, str):
        issues.append(SheetIssue("wrong_type", path, {"expected": "select"}))
    elif raw not in {option.value for option in field.options}:
        issues.append(SheetIssue("unknown_option", path, {"value": raw}))
    else:
        return raw
    return field.default_value


def _image(raw: object, path: str, issues: list[SheetIssue]) -> str | None:
    if raw is None:
        return None
    return _text(raw, path, issues, MAX_IMAGE_REF_LENGTH)


def _resource(
    raw: object, path: str, issues: list[SheetIssue], field: ResourceField
) -> dict[str, int | None]:
    """Значение ресурса: ``{"current": 12}`` или ``{"current": 3, "max": 4}``.

    ``current`` может отсутствовать — тогда ресурс полон. ``max`` хранится, только если
    максимум не считается формулой.
    """
    computed_max = field.max_formula is not None
    value: dict[str, int | None] = {"current": None}
    if not computed_max:
        value["max"] = field.default_max
    if not isinstance(raw, dict):
        issues.append(SheetIssue("wrong_type", path, {"expected": "resource"}))
        return value

    for key in raw:
        if key not in ("current", "max"):
            issues.append(SheetIssue("unknown_key", f"{path}.{key}"))
    if "current" in raw:
        value["current"] = int(_number(raw["current"], f"{path}.current", issues, True, 0))
    if "max" in raw:
        if computed_max:
            issues.append(SheetIssue("max_is_computed", f"{path}.max"))
        else:
            value["max"] = int(_number(raw["max"], f"{path}.max", issues, True, 0))
    return value


def _list(
    raw: object, path: str, issues: list[SheetIssue], field: ListField
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        issues.append(SheetIssue("wrong_type", path, {"expected": "list"}))
        return []
    if len(raw) > MAX_LIST_ITEMS:
        issues.append(SheetIssue("too_long", path, {"length": len(raw), "max": MAX_LIST_ITEMS}))
        return []

    items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if not isinstance(raw_item, dict):
            issues.append(SheetIssue("wrong_type", item_path, {"expected": "list_item"}))
            continue
        issues.extend(
            SheetIssue("unknown_key", f"{item_path}.{key}")
            for key in raw_item
            if key not in field.item
        )
        items.append(
            {
                column_id: _column(raw_item, column_id, column, f"{item_path}.{column_id}", issues)
                for column_id, column in field.item.items()
            }
        )
    return items


def _column(
    item: Mapping[str, Any], column_id: str, column: ListColumn, path: str, issues: list[SheetIssue]
) -> int | float | bool | str:
    present = column_id in item
    raw = item.get(column_id)
    match column.type:
        case "number":
            return _number(raw, path, issues, integer=False) if present else 0
        case "checkbox":
            return _checkbox(raw, path, issues) if present else False
        case "text":
            return _text(raw, path, issues, MAX_TEXT_LENGTH) if present else ""
        case "longtext":
            return _text(raw, path, issues, MAX_LONGTEXT_LENGTH) if present else ""
