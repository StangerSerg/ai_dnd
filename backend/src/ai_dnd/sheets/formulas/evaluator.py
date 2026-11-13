"""Безопасный вычислитель формул.

Формула — выражение над полями карточки персонажа:

    floor((dex - 10) / 2)
    2 + floor((level - 1) / 4)
    prof if stealth_prof else 0

Формулы пишут авторы шаблонов, то есть посторонние люди, поэтому:

- вычисление идёт только через ``simpleeval``, без ``eval``;
- до вычисления формула проверяется по белому списку: разрешены числа, строки, имена
  полей, арифметика, сравнения, ``and`` / ``or`` / ``not``, условие ``a if c else b``
  и функции из ``FUNCTIONS``. Обращения к атрибутам, индексы, вызовы чего-либо ещё,
  возведение в степень и побитовые операции запрещены;
- длина, глубина и размер формулы ограничены.

Строки можно только сравнивать (``armor == "heavy"``), арифметика над ними — ошибка.
"""

import ast
import math
import operator
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TypeGuard

import simpleeval

from ai_dnd.sheets.formulas.errors import FormulaEvaluationError, FormulaSyntaxError

MAX_FORMULA_LENGTH = 500
# Сумма из N слагаемых — это дерево глубиной N, поэтому предел с запасом.
MAX_FORMULA_DEPTH = 50
MAX_FORMULA_NODES = 250
MAX_STRING_LENGTH = 100
# Числа крупнее этого в карточке не нужны, а в JavaScript они ещё и теряют точность.
MAX_NUMBER = 10**15

type Number = int | float
type Value = int | float | bool | str


@dataclass(frozen=True, slots=True)
class Formula:
    """Разобранная и проверенная формула."""

    source: str
    # Имена полей, от которых формула зависит.
    names: frozenset[str]
    tree: ast.expr = field(repr=False, compare=False)


# --- Операторы и функции ---


def _is_number(value: object) -> TypeGuard[Number]:
    # bool — тоже число: «prof * stealth_prof» работает как в Python.
    return isinstance(value, int | float)


def _binary(
    function: Callable[[Number, Number], Number | bool], symbol: str
) -> Callable[[object, object], Number | bool]:
    def checked(left: object, right: object) -> Number | bool:
        if not _is_number(left) or not _is_number(right):
            raise FormulaEvaluationError("type_mismatch", operator=symbol)
        return function(left, right)

    return checked


def _unary(function: Callable[[Number], Number], symbol: str) -> Callable[[object], Number]:
    def checked(operand: object) -> Number:
        if not _is_number(operand):
            raise FormulaEvaluationError("type_mismatch", operator=symbol)
        return function(operand)

    return checked


_BINARY_OPERATORS: dict[type[ast.AST], Callable[..., object]] = {
    ast.Add: _binary(operator.add, "+"),
    ast.Sub: _binary(operator.sub, "-"),
    ast.Mult: _binary(operator.mul, "*"),
    ast.Div: _binary(operator.truediv, "/"),
    ast.FloorDiv: _binary(operator.floordiv, "//"),
    ast.Mod: _binary(operator.mod, "%"),
}
_UNARY_OPERATORS: dict[type[ast.AST], Callable[..., object]] = {
    ast.USub: _unary(operator.neg, "-"),
    ast.UAdd: _unary(operator.pos, "+"),
    ast.Not: operator.not_,
}
_COMPARISONS: dict[type[ast.AST], Callable[..., object]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: _binary(operator.lt, "<"),
    ast.LtE: _binary(operator.le, "<="),
    ast.Gt: _binary(operator.gt, ">"),
    ast.GtE: _binary(operator.ge, ">="),
}
_OPERATORS: dict[type[ast.AST], Callable[..., object]] = {
    **_BINARY_OPERATORS,
    **_UNARY_OPERATORS,
    **_COMPARISONS,
}


def _round(value: Number) -> int:
    """Округление «как в школе»: 2.5 → 3, −2.5 → −3 (а не к чётному, как в Python)."""
    rounded = math.floor(abs(value) + 0.5)
    return -rounded if value < 0 else rounded


def _clamp(value: Number, low: Number, high: Number) -> Number:
    """Значение, зажатое в отрезок [low, high]."""
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class _Function:
    implementation: Callable[..., Number]
    min_args: int
    # None — сколько угодно.
    max_args: int | None


_FUNCTION_SPECS: dict[str, _Function] = {
    "floor": _Function(math.floor, 1, 1),
    "ceil": _Function(math.ceil, 1, 1),
    "round": _Function(_round, 1, 1),
    "abs": _Function(abs, 1, 1),
    "min": _Function(min, 2, None),
    "max": _Function(max, 2, None),
    "clamp": _Function(_clamp, 3, 3),
}

# Имена разрешённых функций — для подсказок в редакторе формул.
FUNCTIONS: frozenset[str] = frozenset(_FUNCTION_SPECS)


def _checked_function(name: str, implementation: Callable[..., Number]) -> Callable[..., Number]:
    def checked(*args: object) -> Number:
        if not all(_is_number(arg) for arg in args):
            raise FormulaEvaluationError("type_mismatch", function=name)
        return implementation(*args)

    return checked


_FUNCTIONS: dict[str, Callable[..., Number]] = {
    name: _checked_function(name, spec.implementation) for name, spec in _FUNCTION_SPECS.items()
}


# --- Разбор и проверка ---


@lru_cache(maxsize=2048)
def parse_formula(source: str) -> Formula:
    """Разобрать формулу и проверить её по белому списку конструкций."""
    if len(source) > MAX_FORMULA_LENGTH:
        raise FormulaSyntaxError("too_long", length=len(source), max=MAX_FORMULA_LENGTH)
    text = source.strip()
    if not text:
        raise FormulaSyntaxError("empty")

    try:
        with warnings.catch_warnings():
            # Предупреждения Python о стиле записи автору шаблона ни к чему.
            warnings.simplefilter("ignore")
            tree = ast.parse(text, mode="eval").body
    except SyntaxError as error:
        raise FormulaSyntaxError("syntax_error", position=error.offset, detail=error.msg) from None
    except (ValueError, RecursionError, MemoryError):
        raise FormulaSyntaxError("syntax_error", position=None, detail=None) from None

    return Formula(source=text, names=_validate(tree), tree=tree)


def _validate(tree: ast.expr) -> frozenset[str]:
    """Проверить дерево формулы и вернуть имена, на которые она ссылается.

    Обход без рекурсии: глубину дерева задаёт автор формулы.
    """
    names: set[str] = set()
    stack: list[tuple[ast.expr, int]] = [(tree, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_FORMULA_DEPTH:
            raise FormulaSyntaxError("too_deep", max=MAX_FORMULA_DEPTH)
        if count > MAX_FORMULA_NODES:
            raise FormulaSyntaxError("too_complex", max=MAX_FORMULA_NODES)
        stack.extend((child, depth + 1) for child in _check_node(node, names))
    return frozenset(names)


def _check_node(node: ast.expr, names: set[str]) -> list[ast.expr]:
    """Проверить один узел и вернуть вложенные узлы для дальнейшей проверки."""
    match node:
        case ast.Constant(value=value):
            _check_constant(value)
            return []
        case ast.Name(id=name):
            if name in _FUNCTION_SPECS:
                # Функцию можно только вызвать: «floor + 1» — ошибка.
                raise FormulaSyntaxError("function_as_value", name=name)
            if name.startswith("_"):
                raise FormulaSyntaxError("forbidden_name", name=name)
            names.add(name)
            return []
        case ast.BinOp(left=left, op=op, right=right):
            _check_operator(op, _BINARY_OPERATORS)
            return [left, right]
        case ast.UnaryOp(op=op, operand=operand):
            _check_operator(op, _UNARY_OPERATORS)
            return [operand]
        case ast.BoolOp(values=values):
            return list(values)
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            for comparison in ops:
                _check_operator(comparison, _COMPARISONS)
            return [left, *comparators]
        case ast.IfExp(test=test, body=body, orelse=orelse):
            return [test, body, orelse]
        case ast.Call(func=function, args=args, keywords=keywords):
            _check_call(function, len(args), has_keywords=bool(keywords))
            return list(args)
        case _:
            raise FormulaSyntaxError("forbidden_syntax", construct=type(node).__name__)


def _check_constant(value: object) -> None:
    if isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            # «1e999» Python молча превращает в бесконечность.
            raise FormulaSyntaxError("number_too_large")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise FormulaSyntaxError("string_too_long", length=len(value), max=MAX_STRING_LENGTH)
        return
    # None, bytes, комплексные числа, «...».
    raise FormulaSyntaxError("forbidden_syntax", construct=type(value).__name__)


def _check_operator(op: ast.AST, allowed: Mapping[type[ast.AST], object]) -> None:
    if type(op) not in allowed:
        raise FormulaSyntaxError("forbidden_operator", operator=type(op).__name__)


def _check_call(function: ast.expr, arg_count: int, *, has_keywords: bool) -> None:
    if not isinstance(function, ast.Name):
        # Вызов метода или результата другого вызова: «x.bit_length()», «f()()».
        raise FormulaSyntaxError("forbidden_syntax", construct="Call")
    spec = _FUNCTION_SPECS.get(function.id)
    if spec is None:
        raise FormulaSyntaxError("unknown_function", name=function.id)
    if has_keywords:
        raise FormulaSyntaxError("forbidden_syntax", construct="keyword")
    if arg_count < spec.min_args or (spec.max_args is not None and arg_count > spec.max_args):
        raise FormulaSyntaxError(
            "wrong_argument_count",
            name=function.id,
            count=arg_count,
            min=spec.min_args,
            max=spec.max_args,
        )


# --- Вычисление ---


def evaluate(formula: Formula | str, values: Mapping[str, Value]) -> Value:
    """Вычислить формулу. ``values`` — значения полей, на которые она ссылается.

    Число с нулевой дробной частью возвращается целым: ``10 / 2`` — это ``5``, а не ``5.0``.
    """
    parsed = parse_formula(formula) if isinstance(formula, str) else formula

    names: dict[str, Value] = {}
    # По алфавиту, чтобы при нескольких проблемах ошибка всегда была одна и та же.
    for name in sorted(parsed.names):
        if name not in values:
            raise FormulaEvaluationError("unknown_name", name=name)
        names[name] = _check_input(name, values[name])

    evaluator = simpleeval.SimpleEval(operators=_OPERATORS, functions=_FUNCTIONS, names=names)
    try:
        result = evaluator.eval(parsed.source, previously_parsed=parsed.tree)
    except FormulaEvaluationError:
        raise
    except ZeroDivisionError:
        raise FormulaEvaluationError("division_by_zero") from None
    except (OverflowError, ValueError):
        # Например, floor() от бесконечности или слишком большое целое при делении.
        raise FormulaEvaluationError("number_too_large") from None
    except simpleeval.InvalidExpression as error:
        # До сюда дойти не должно: всё запрещённое отсеяно при разборе.
        raise FormulaEvaluationError("evaluation_failed", detail=str(error)) from error
    return _check_result(result)


def _check_input(name: str, value: object) -> Value:
    if isinstance(value, str):
        return value
    if _is_number(value) and math.isfinite(value) and abs(value) <= MAX_NUMBER:
        return value
    raise FormulaEvaluationError("unsupported_value", name=name, type=type(value).__name__)


def _check_result(value: object) -> Value:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FormulaEvaluationError("number_too_large")
        if value.is_integer():
            value = int(value)
    if _is_number(value):
        if abs(value) > MAX_NUMBER:
            raise FormulaEvaluationError("number_too_large")
        return value
    raise FormulaEvaluationError("unsupported_result", type=type(value).__name__)
