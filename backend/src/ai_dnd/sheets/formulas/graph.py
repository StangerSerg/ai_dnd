"""Граф зависимостей формул: в каком порядке считать вычисляемые поля.

Вычисляемое поле может ссылаться на обычные поля и на другие вычисляемые:

    dex_mod = floor((dex - 10) / 2)
    ac      = 10 + dex_mod

Граф находит порядок вычисления (сначала ``dex_mod``, потом ``ac``) и все проблемы
набора формул разом: ошибки в самих формулах, ссылки на несуществующие поля и циклы.
"""

import heapq
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from ai_dnd.sheets.formulas.errors import (
    FormulaEvaluationError,
    FormulaGraphError,
    FormulaIssue,
    FormulaSyntaxError,
)
from ai_dnd.sheets.formulas.evaluator import Formula, Value, evaluate, parse_formula


@dataclass(frozen=True, slots=True)
class _Analysis:
    formulas: dict[str, Formula]
    order: tuple[str, ...]
    issues: tuple[FormulaIssue, ...]


def find_issues(formulas: Mapping[str, str], inputs: Collection[str] = ()) -> list[FormulaIssue]:
    """Все проблемы набора формул. Пустой список — набор можно вычислять.

    ``formulas`` — формулы вычисляемых полей по id поля, ``inputs`` — id обычных полей.
    """
    return list(_analyze(formulas, inputs).issues)


class FormulaGraph:
    """Проверенный набор формул, готовый к вычислению."""

    def __init__(self, formulas: Mapping[str, str], inputs: Collection[str] = ()) -> None:
        """Построить граф. Если в наборе есть проблемы — ``FormulaGraphError`` со всеми сразу."""
        analysis = _analyze(formulas, inputs)
        if analysis.issues:
            raise FormulaGraphError(analysis.issues)
        self._formulas = analysis.formulas
        self.inputs = frozenset(inputs)
        # Порядок вычисления: каждое поле идёт после всех, от которых зависит.
        self.order = analysis.order

    def dependencies(self, field: str) -> frozenset[str]:
        """Поля (обычные и вычисляемые), на которые формула поля ссылается напрямую."""
        return self._formulas[field].names

    def evaluate(self, values: Mapping[str, Value]) -> dict[str, Value]:
        """Посчитать все вычисляемые поля по значениям обычных.

        Возвращает только вычисляемые поля. Если в ``values`` затесались их старые
        значения, они игнорируются.
        """
        scope = {name: value for name, value in values.items() if name not in self._formulas}
        computed: dict[str, Value] = {}
        for field in self.order:
            try:
                value = evaluate(self._formulas[field], scope)
            except FormulaEvaluationError as error:
                raise FormulaEvaluationError(error.code, field=field, **error.params) from None
            scope[field] = computed[field] = value
        return computed


def _analyze(formulas: Mapping[str, str], inputs: Collection[str]) -> _Analysis:
    input_names = set(inputs)
    issues: list[FormulaIssue] = []
    parsed: dict[str, Formula] = {}

    # Везде обходим поля по алфавиту: результат не должен зависеть от порядка в словаре.
    for field in sorted(formulas):
        if field in input_names:
            issues.append(FormulaIssue("name_conflict", field))
        try:
            parsed[field] = parse_formula(formulas[field])
        except FormulaSyntaxError as error:
            issues.append(FormulaIssue(error.code, field, dict(error.params)))

    computed_names = set(formulas)
    known = input_names | computed_names
    for field, formula in parsed.items():
        issues.extend(
            FormulaIssue("unknown_reference", field, {"name": name})
            for name in sorted(formula.names - known)
        )

    # Зависимости только между вычисляемыми полями: обычные поля уже «посчитаны».
    dependencies = {
        field: sorted(parsed[field].names & computed_names) if field in parsed else []
        for field in sorted(formulas)
    }
    order = _topological_order(dependencies)
    issues.extend(
        FormulaIssue("cycle", cycle[0], {"cycle": cycle})
        for cycle in _find_cycles(dependencies, resolved=set(order))
    )

    return _Analysis(
        formulas={field: parsed[field] for field in order if field in parsed},
        order=tuple(order),
        issues=tuple(issues),
    )


def _topological_order(dependencies: Mapping[str, list[str]]) -> list[str]:
    """Порядок, в котором каждое поле идёт после своих зависимостей (алгоритм Кана).

    Поля, которые входят в цикл или зависят от него, в результат не попадают.
    """
    waiting_for = {field: len(deps) for field, deps in dependencies.items()}
    dependents: dict[str, list[str]] = {field: [] for field in dependencies}
    for field, deps in dependencies.items():
        for dependency in deps:
            dependents[dependency].append(field)

    # Куча вместо очереди: из готовых полей всегда берём первое по алфавиту.
    ready = [field for field, count in waiting_for.items() if count == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        field = heapq.heappop(ready)
        order.append(field)
        for dependent in dependents[field]:
            waiting_for[dependent] -= 1
            if waiting_for[dependent] == 0:
                heapq.heappush(ready, dependent)
    return order


def _find_cycles(dependencies: Mapping[str, list[str]], resolved: set[str]) -> list[list[str]]:
    """Циклы среди полей, которые не удалось упорядочить.

    Цикл ``[a, b, c]`` читается так: ``a`` зависит от ``b``, ``b`` — от ``c``, ``c`` — от ``a``.
    Начинается цикл с первого по алфавиту поля.
    """
    stuck = sorted(set(dependencies) - resolved)
    stuck_set = set(stuck)
    # У каждого застрявшего поля есть хотя бы одна застрявшая зависимость: идём по первой.
    next_field = {
        field: next(dep for dep in dependencies[field] if dep in stuck_set) for field in stuck
    }

    cycles: list[list[str]] = []
    visited: set[str] = set()
    for start in stuck:
        path: list[str] = []
        position: dict[str, int] = {}
        field = start
        while field not in visited:
            visited.add(field)
            position[field] = len(path)
            path.append(field)
            field = next_field[field]
        if field in position:
            # Вернулись в поле из текущего пути — нашли новый цикл.
            cycle = path[position[field] :]
            smallest = cycle.index(min(cycle))
            cycles.append(cycle[smallest:] + cycle[:smallest])
    return cycles
