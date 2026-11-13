import pytest

from ai_dnd.sheets.formulas import (
    FUNCTIONS,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_LENGTH,
    MAX_FORMULA_NODES,
    FormulaEvaluationError,
    FormulaSyntaxError,
    Value,
    evaluate,
    parse_formula,
)

# --- Вычисление ---


@pytest.mark.parametrize(
    ("dex", "modifier"),
    [(1, -5), (8, -1), (9, -1), (10, 0), (11, 0), (15, 2), (20, 5), (30, 10)],
)
def test_ability_modifier(dex: int, modifier: int) -> None:
    assert evaluate("floor((dex - 10) / 2)", {"dex": dex}) == modifier


@pytest.mark.parametrize(("level", "bonus"), [(1, 2), (4, 2), (5, 3), (9, 4), (17, 6), (20, 6)])
def test_proficiency_bonus(level: int, bonus: int) -> None:
    assert evaluate("2 + floor((level - 1) / 4)", {"level": level}) == bonus


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("-x + 10", 6),
        ("+x", 4),
        ("7 // 2", 3),
        ("7 % 3", 1),
        ("7 / 2", 3.5),
        ("10 / 2", 5),
        ("0.5 + 0.25", 0.75),
        ("floor(2.9)", 2),
        ("floor(-2.1)", -3),
        ("ceil(2.1)", 3),
        ("abs(-7)", 7),
        ("min(x, 3)", 3),
        ("max(x, 3, 10)", 10),
        ("clamp(x, 5, 9)", 5),
        ("clamp(12, 5, 9)", 9),
        ("clamp(7, 5, 9)", 7),
        ("x > 3", True),
        ("x >= 5", False),
        ("1 < x < 5", True),
        ("x == 4 and not flag", True),
        ("flag or x", 4),
        ("x if flag else 0", 0),
        ("0 if flag else x * 2", 8),
        ("x * (not flag)", 4),
        ('armor == "heavy"', True),
        ('armor != "heavy"', False),
        ('10 if armor == "light" else 14', 14),
        ("True and x", 4),
    ],
)
def test_expressions(formula: str, expected: Value) -> None:
    values: dict[str, Value] = {"x": 4, "flag": False, "armor": "heavy"}

    result = evaluate(formula, values)

    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("value", "rounded"),
    [(2.4, 2), (2.5, 3), (3.5, 4), (-2.4, -2), (-2.5, -3), (0.5, 1), (7, 7)],
)
def test_round_goes_half_away_from_zero(value: float, rounded: int) -> None:
    assert evaluate("round(x)", {"x": value}) == rounded


def test_checkbox_works_as_number() -> None:
    values: dict[str, Value] = {"dex_mod": 2, "prof": 3, "stealth_prof": True}

    assert evaluate("dex_mod + (prof if stealth_prof else 0)", values) == 5
    assert evaluate("dex_mod + prof * stealth_prof", values) == 5


def test_unused_branch_is_not_evaluated() -> None:
    assert evaluate("total / count if count else 0", {"total": 10, "count": 0}) == 0
    assert evaluate("count and total / count", {"total": 10, "count": 0}) == 0


def test_cyrillic_field_names() -> None:
    assert evaluate("floor((ловкость - 10) / 2)", {"ловкость": 14}) == 2


def test_extra_values_are_ignored() -> None:
    assert evaluate("x + 1", {"x": 1, "y": 100, "notes": "текст"}) == 2


def test_parsed_formula_reports_its_dependencies() -> None:
    formula = parse_formula("  max(str_mod, dex_mod) + (prof if athletics_prof else 0) + 2 ")

    assert formula.source == "max(str_mod, dex_mod) + (prof if athletics_prof else 0) + 2"
    assert formula.names == {"str_mod", "dex_mod", "prof", "athletics_prof"}
    assert evaluate(formula, {"str_mod": 3, "dex_mod": 1, "prof": 2, "athletics_prof": True}) == 7


def test_whitelist_of_functions_is_exposed() -> None:
    assert frozenset({"floor", "ceil", "round", "abs", "min", "max", "clamp"}) == FUNCTIONS


# --- Запрещённые конструкции ---


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("1 +", "syntax_error"),
        ("(1 + 2", "syntax_error"),
        ("1 2", "syntax_error"),
        ("x = 1", "syntax_error"),
        ("x; y", "syntax_error"),
        ("import os", "syntax_error"),
        ("x\ny", "syntax_error"),
        # Доступ к атрибутам и методам — главный путь к «побегу из песочницы».
        ("x.__class__", "forbidden_syntax"),
        ("x.real", "forbidden_syntax"),
        ("x.bit_length()", "forbidden_syntax"),
        ("().__class__.__bases__[0].__subclasses__()", "forbidden_syntax"),
        ('"".join(x)', "forbidden_syntax"),
        ("x[0]", "forbidden_syntax"),
        ("x[1:2]", "forbidden_syntax"),
        ("[1, 2]", "forbidden_syntax"),
        ("(1, 2)", "forbidden_syntax"),
        ("{1: 2}", "forbidden_syntax"),
        ("{1, 2}", "forbidden_syntax"),
        ("[i for i in x]", "forbidden_syntax"),
        ("lambda: 1", "forbidden_syntax"),
        ("(y := 5)", "forbidden_syntax"),
        ('f"{x}"', "forbidden_syntax"),
        ("max(1, *x)", "forbidden_syntax"),
        ("max(1, 2, key=x)", "forbidden_syntax"),
        ("floor(1)(2)", "forbidden_syntax"),
        ("None", "forbidden_syntax"),
        ('b"abc"', "forbidden_syntax"),
        ("1j", "forbidden_syntax"),
        ("...", "forbidden_syntax"),
        ("await x", "forbidden_syntax"),
        # Вызывать можно только функции из белого списка.
        ("__import__('os')", "unknown_function"),
        ("eval('1')", "unknown_function"),
        ("open('/etc/passwd')", "unknown_function"),
        ("getattr(x, 'real')", "unknown_function"),
        ("int(x)", "unknown_function"),
        ("rand()", "unknown_function"),
        ("x()", "unknown_function"),
        ("floor", "function_as_value"),
        ("floor + 1", "function_as_value"),
        ("max(min, 1)", "function_as_value"),
        ("__builtins__", "forbidden_name"),
        ("_private + 1", "forbidden_name"),
        ("floor()", "wrong_argument_count"),
        ("floor(1, 2)", "wrong_argument_count"),
        ("min(1)", "wrong_argument_count"),
        ("clamp(1, 2)", "wrong_argument_count"),
        # Степень и побитовые операции могут породить гигантские числа.
        ("2 ** 10", "forbidden_operator"),
        ("9 ** 9 ** 9", "forbidden_operator"),
        ("1 << 100", "forbidden_operator"),
        ("x | 1", "forbidden_operator"),
        ("x & 1", "forbidden_operator"),
        ("x ^ 1", "forbidden_operator"),
        ("~x", "forbidden_operator"),
        ("x @ x", "forbidden_operator"),
        ("x in y", "forbidden_operator"),
        ("x is y", "forbidden_operator"),
        ("x not in y", "forbidden_operator"),
        ("1e999", "number_too_large"),
        ('x == "' + "a" * 101 + '"', "string_too_long"),
    ],
)
def test_forbidden_formulas_are_rejected(formula: str, code: str) -> None:
    with pytest.raises(FormulaSyntaxError) as error:
        parse_formula(formula)

    assert error.value.code == code


def test_formula_length_is_limited() -> None:
    longest = "1" * MAX_FORMULA_LENGTH
    assert evaluate(longest[:15], {}) == 111111111111111
    parse_formula(longest)

    with pytest.raises(FormulaSyntaxError) as error:
        parse_formula(longest + "1")

    assert error.value.code == "too_long"
    assert error.value.params == {"length": MAX_FORMULA_LENGTH + 1, "max": MAX_FORMULA_LENGTH}


def test_formula_depth_is_limited() -> None:
    # Цепочка унарных минусов: коротко, но глубоко — без предела это переполнение стека.
    parse_formula("-" * (MAX_FORMULA_DEPTH - 1) + "x")

    with pytest.raises(FormulaSyntaxError) as error:
        parse_formula("-" * MAX_FORMULA_DEPTH + "x")

    assert error.value.code == "too_deep"


def test_long_sum_fits_into_depth_limit() -> None:
    names = [f"f{index}" for index in range(40)]

    assert evaluate(" + ".join(names), dict.fromkeys(names, 1)) == 40


def test_formula_size_is_limited() -> None:
    # Широкое, но неглубокое выражение: много аргументов у max(), в каждом по два узла.
    arguments = ",".join(["-1"] * (MAX_FORMULA_NODES // 2 + 1))

    with pytest.raises(FormulaSyntaxError) as error:
        parse_formula(f"max({arguments})")

    assert error.value.code == "too_complex"


def test_syntax_error_reports_position() -> None:
    with pytest.raises(FormulaSyntaxError) as error:
        parse_formula("dex +* 2")

    assert error.value.code == "syntax_error"
    assert error.value.params["position"] == 6


# --- Ошибки вычисления ---


@pytest.mark.parametrize(
    ("formula", "values", "code"),
    [
        ("x + missing", {"x": 1}, "unknown_name"),
        ("1 if x else missing", {"x": 1}, "unknown_name"),
        ("1 / x", {"x": 0}, "division_by_zero"),
        ("1 // x", {"x": 0}, "division_by_zero"),
        ("1 % x", {"x": 0}, "division_by_zero"),
        ("x + 1", {"x": "текст"}, "type_mismatch"),
        ('"a" + "b"', {}, "type_mismatch"),
        ('"ab" * 1000', {}, "type_mismatch"),
        ("-x", {"x": "текст"}, "type_mismatch"),
        ('x < "b"', {"x": "a"}, "type_mismatch"),
        ("floor(x)", {"x": "2.5"}, "type_mismatch"),
        ("max(1, x)", {"x": "2"}, "type_mismatch"),
        ("x * x * x", {"x": 10**15}, "number_too_large"),
        ("x * 10", {"x": 1e300}, "unsupported_value"),
        ("x + 1", {"x": float("nan")}, "unsupported_value"),
        ("x + 1", {"x": float("inf")}, "unsupported_value"),
        ("x + 1", {"x": [1, 2]}, "unsupported_value"),
        ("x + 1", {"x": None}, "unsupported_value"),
        ("x + 1", {"x": {"current": 5}}, "unsupported_value"),
    ],
)
def test_evaluation_errors(formula: str, values: dict[str, Value], code: str) -> None:
    with pytest.raises(FormulaEvaluationError) as error:
        evaluate(formula, values)

    assert error.value.code == code


def test_error_names_the_missing_field() -> None:
    with pytest.raises(FormulaEvaluationError) as error:
        evaluate("zeta + alpha", {})

    # При нескольких неизвестных именах называется первое по алфавиту — всегда одно и то же.
    assert error.value.params == {"name": "alpha"}
    assert str(error.value) == "unknown_name: name='alpha'"
