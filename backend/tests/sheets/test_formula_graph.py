import random

import pytest

from ai_dnd.sheets.formulas import (
    FormulaEvaluationError,
    FormulaGraph,
    FormulaGraphError,
    FormulaIssue,
    Value,
    find_issues,
)

INPUTS = ["level", "dex", "wis", "stealth_prof", "armor_bonus"]

# Кусочек карточки 5e: вычисляемые поля ссылаются друг на друга в несколько слоёв.
FORMULAS = {
    "passive_perception": "10 + wis_mod",
    "ac": "10 + dex_mod + armor_bonus",
    "stealth": "dex_mod + (prof if stealth_prof else 0)",
    "wis_mod": "floor((wis - 10) / 2)",
    "prof": "2 + floor((level - 1) / 4)",
    "dex_mod": "floor((dex - 10) / 2)",
}

VALUES: dict[str, Value] = {
    "level": 5,
    "dex": 16,
    "wis": 9,
    "stealth_prof": True,
    "armor_bonus": 2,
}


def test_computes_fields_in_dependency_order() -> None:
    graph = FormulaGraph(FORMULAS, INPUTS)

    assert graph.evaluate(VALUES) == {
        "dex_mod": 3,
        "wis_mod": -1,
        "prof": 3,
        "ac": 15,
        "stealth": 6,
        "passive_perception": 9,
    }


def test_order_puts_dependencies_first() -> None:
    graph = FormulaGraph(FORMULAS, INPUTS)

    assert graph.order == ("dex_mod", "ac", "prof", "stealth", "wis_mod", "passive_perception")
    for position, field in enumerate(graph.order):
        computed_dependencies = graph.dependencies(field) & set(FORMULAS)
        assert computed_dependencies <= set(graph.order[:position])


def test_order_does_not_depend_on_dictionary_order() -> None:
    expected = FormulaGraph(FORMULAS, INPUTS).order
    items = list(FORMULAS.items())

    for seed in range(20):
        random.Random(seed).shuffle(items)
        assert FormulaGraph(dict(items), INPUTS).order == expected


def test_dependencies_include_plain_and_computed_fields() -> None:
    graph = FormulaGraph(FORMULAS, INPUTS)

    assert graph.dependencies("stealth") == {"dex_mod", "prof", "stealth_prof"}
    assert graph.dependencies("dex_mod") == {"dex"}


def test_stale_computed_values_are_ignored() -> None:
    graph = FormulaGraph(FORMULAS, INPUTS)

    result = graph.evaluate({**VALUES, "dex_mod": 100, "ac": 100})

    assert result["dex_mod"] == 3
    assert result["ac"] == 15


def test_empty_graph() -> None:
    graph = FormulaGraph({}, ["str"])

    assert graph.order == ()
    assert graph.evaluate({"str": 10}) == {}


def test_formulas_without_inputs() -> None:
    assert FormulaGraph({"b": "a * 2", "a": "21"}).evaluate({}) == {"a": 21, "b": 42}


# --- Проблемы набора формул ---


def test_valid_formulas_have_no_issues() -> None:
    assert find_issues(FORMULAS, INPUTS) == []


def test_unknown_reference_is_reported() -> None:
    issues = find_issues({"dex_mod": "floor((dexterity - 10) / 2) + bonus"}, ["dex"])

    assert issues == [
        FormulaIssue("unknown_reference", "dex_mod", {"name": "bonus"}),
        FormulaIssue("unknown_reference", "dex_mod", {"name": "dexterity"}),
    ]


def test_broken_formula_is_reported_with_its_field() -> None:
    issues = find_issues({"ac": "10 +", "evil": "dex.__class__"}, ["dex"])

    assert [(issue.field, issue.code) for issue in issues] == [
        ("ac", "syntax_error"),
        ("evil", "forbidden_syntax"),
    ]
    assert issues[1].params == {"construct": "Attribute"}


def test_two_field_cycle_is_found() -> None:
    issues = find_issues({"a": "b + 1", "b": "a + 1"})

    assert issues == [FormulaIssue("cycle", "a", {"cycle": ["a", "b"]})]


def test_long_cycle_is_found_and_keeps_direction() -> None:
    # c зависит от a, a — от b, b — от c.
    issues = find_issues({"c": "a + x", "a": "b * 2", "b": "floor(c / 2)"}, ["x"])

    assert issues == [FormulaIssue("cycle", "a", {"cycle": ["a", "b", "c"]})]


def test_self_reference_is_a_cycle() -> None:
    assert find_issues({"hp_max": "hp_max + 1"}) == [
        FormulaIssue("cycle", "hp_max", {"cycle": ["hp_max"]})
    ]


def test_separate_cycles_are_all_found() -> None:
    issues = find_issues(
        {
            "a": "b",
            "b": "a",
            "x": "y + ok",
            "y": "z",
            "z": "x",
            "ok": "level * 2",
            # Зависит от цикла, но сам в него не входит — отдельной проблемой не считается.
            "after_cycle": "a + 1",
        },
        ["level"],
    )

    assert issues == [
        FormulaIssue("cycle", "a", {"cycle": ["a", "b"]}),
        FormulaIssue("cycle", "x", {"cycle": ["x", "y", "z"]}),
    ]


def test_field_cannot_be_both_plain_and_computed() -> None:
    assert find_issues({"dex": "10"}, ["dex"]) == [FormulaIssue("name_conflict", "dex")]


def test_graph_reports_all_issues_at_once() -> None:
    formulas = {
        "a": "b",
        "b": "a",
        "broken": "1 +",
        "lost": "nowhere * 2",
    }

    with pytest.raises(FormulaGraphError) as error:
        FormulaGraph(formulas, ["level"])

    assert [(issue.field, issue.code) for issue in error.value.issues] == [
        ("broken", "syntax_error"),
        ("lost", "unknown_reference"),
        ("a", "cycle"),
    ]
    assert error.value.code == "invalid_formulas"
    assert "lost: unknown_reference (name='nowhere')" in str(error.value)


def test_long_chain_does_not_overflow_the_stack() -> None:
    # 2000 полей, каждое зависит от предыдущего: рекурсивный обход здесь бы упал.
    formulas = {f"f{index:04}": f"f{index - 1:04} + 1" for index in range(1, 2000)}
    formulas["f0000"] = "start"

    graph = FormulaGraph(formulas, ["start"])

    assert graph.evaluate({"start": 0})["f1999"] == 1999

    formulas["f0000"] = "f1999"
    (issue,) = find_issues(formulas, ["start"])
    assert issue.code == "cycle"
    assert len(issue.params["cycle"]) == 2000  # type: ignore[arg-type]


# --- Ошибки вычисления ---


def test_evaluation_error_names_the_field() -> None:
    graph = FormulaGraph(
        {"share": "gold / party_size", "double": "share * 2"}, ["gold", "party_size"]
    )

    with pytest.raises(FormulaEvaluationError) as error:
        graph.evaluate({"gold": 100, "party_size": 0})

    assert error.value.code == "division_by_zero"
    assert error.value.params == {"field": "share"}


def test_missing_input_value_is_an_error() -> None:
    graph = FormulaGraph(FORMULAS, INPUTS)
    values = {name: value for name, value in VALUES.items() if name != "wis"}

    with pytest.raises(FormulaEvaluationError) as error:
        graph.evaluate(values)

    assert error.value.code == "unknown_name"
    assert error.value.params == {"field": "wis_mod", "name": "wis"}
