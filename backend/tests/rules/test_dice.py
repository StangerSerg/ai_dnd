import pytest

from ai_dnd.rules.dice import (
    MAX_DICE_PER_TERM,
    MAX_NOTATION_LENGTH,
    MAX_SIDES,
    MAX_TERMS,
    ConstantTerm,
    DiceExpression,
    DiceTerm,
    DieRoll,
    Keep,
    KeepMode,
    RollMode,
    RollResult,
    parse,
    roll,
)
from ai_dnd.rules.errors import DiceNotationError
from ai_dnd.rules.rng import ScriptedRng, SeededRng

# --- Разбор ---


@pytest.mark.parametrize(
    ("notation", "canonical"),
    [
        ("2d6+3", "2d6 + 3"),
        ("2к6+3", "2d6 + 3"),
        ("2д6 + 3", "2d6 + 3"),
        ("2К6+3", "2d6 + 3"),
        ("d20", "1d20"),
        ("к20", "1d20"),
        ("D20 + 5", "1d20 + 5"),
        ("  1d20+5 - 1d4 ", "1d20 + 5 - 1d4"),
        ("1d20 − 2", "1d20 - 2"),
        ("-1d4+3", "-1d4 + 3"),
        ("+5", "5"),
        ("7", "7"),
        ("4d6kh3", "4d6kh3"),
        ("4D6KH3", "4d6kh3"),
        ("2d20kl1", "2d20kl1"),
        ("2d20kh", "2d20kh1"),
        ("d%", "1d100"),
        ("2d%+1", "2d100 + 1"),
        ("1d20+5 adv", "1d20 + 5 adv"),
        ("1d20 advantage", "1d20 adv"),
        ("1d20dis", "1d20 dis"),
        ("1к20 преимущество", "1d20 adv"),
        ("1к20+2 помеха", "1d20 + 2 dis"),
    ],
)
def test_parse_gives_canonical_notation(notation: str, canonical: str) -> None:
    assert str(parse(notation)) == canonical


def test_parse_builds_terms() -> None:
    expression = parse("4d6kh3 - 2 adv")

    assert expression == DiceExpression(
        terms=(
            DiceTerm(count=4, sides=6, keep=Keep(KeepMode.HIGHEST, 3)),
            ConstantTerm(value=2, sign=-1),
        ),
        mode=RollMode.ADVANTAGE,
    )


def test_canonical_notation_parses_back_to_the_same_expression() -> None:
    expression = parse("2к20kl1 - 3 + к4 помеха")

    assert parse(str(expression)) == expression


@pytest.mark.parametrize(
    ("notation", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("adv", "unexpected_token"),
        ("abc", "unknown_word"),
        ("2d6 + abc", "unknown_word"),
        ("2d", "unknown_word"),
        ("2d6 * 2", "unexpected_character"),
        ("(2d6)", "unexpected_character"),
        ("2d6.5", "unexpected_character"),
        ("2d6 3", "unexpected_token"),
        ("2d6 1d4", "unexpected_token"),
        ("2d6++3", "unexpected_token"),
        ("2d6+-3", "unexpected_token"),
        ("2d6+", "unexpected_end"),
        ("-", "unexpected_end"),
        ("2d6 + adv", "unexpected_token"),
        ("1d20 adv + 5", "unexpected_token"),
        ("1d20 adv dis", "unexpected_token"),
        ("0d6", "dice_count_out_of_range"),
        (f"{MAX_DICE_PER_TERM + 1}d6", "dice_count_out_of_range"),
        ("2d0", "dice_sides_out_of_range"),
        ("2d1", "dice_sides_out_of_range"),
        (f"1d{MAX_SIDES + 1}", "dice_sides_out_of_range"),
        ("2d6kh3", "keep_out_of_range"),
        ("2d6kl0", "keep_out_of_range"),
        ("1d20 + 1000001", "constant_out_of_range"),
        ("1+" * MAX_TERMS + "1", "too_many_terms"),
        ("1d20" + " " * MAX_NOTATION_LENGTH, "too_long"),
    ],
)
def test_parse_rejects_bad_notation(notation: str, code: str) -> None:
    with pytest.raises(DiceNotationError) as error:
        parse(notation)

    assert error.value.code == code


def test_error_carries_parameters_for_translation() -> None:
    with pytest.raises(DiceNotationError) as error:
        parse("2d6 # 3")

    assert error.value.code == "unexpected_character"
    assert error.value.params == {"character": "#", "position": 4}


def test_constant_term_from_signed_number() -> None:
    assert ConstantTerm.signed(-1, label="dex_mod") == ConstantTerm(
        value=1, sign=-1, label="dex_mod"
    )
    assert ConstantTerm.signed(3) == ConstantTerm(value=3, sign=1)


# --- Бросок ---


def test_roll_russian_notation_with_scripted_dice() -> None:
    result = roll("2к6+3", ScriptedRng([4, 2]))

    assert result.total == 9
    assert result.notation == "2d6 + 3"
    assert result.mode is RollMode.NORMAL
    assert result.dice_total == 6
    assert result.modifier_total == 3
    assert str(result) == "2d6 + 3 → [4, 2] + 3 = 9"


def test_roll_uses_system_rng_by_default() -> None:
    for _ in range(50):
        assert 5 <= roll("2к6+3").total <= 15


def test_roll_subtracts_negative_terms() -> None:
    result = roll("1d20 - 1d4 - 2", ScriptedRng([10, 3]))

    assert result.total == 5
    assert [term.subtotal for term in result.terms] == [10, -3, -2]
    assert str(result) == "1d20 - 1d4 - 2 → [10] - [3] - 2 = 5"


def test_roll_keeps_highest_dice() -> None:
    result = roll("4d6kh3", ScriptedRng([3, 1, 6, 4]))

    assert result.total == 13
    assert [(die.value, die.kept) for die in _dice(result)] == [
        (3, True),
        (1, False),
        (6, True),
        (4, True),
    ]
    assert str(result) == "4d6kh3 → [3, (1), 6, 4] = 13"


def test_roll_keeps_lowest_die() -> None:
    result = roll("2d20kl1 + 5", ScriptedRng([17, 4]))

    assert result.total == 9
    assert [die.kept for die in _dice(result)] == [False, True]


def test_keep_prefers_the_earlier_of_equal_dice() -> None:
    result = roll("3d6kh1", ScriptedRng([5, 5, 2]))

    assert [die.kept for die in _dice(result)] == [True, False, False]
    assert result.total == 5


@pytest.mark.parametrize(
    ("mode", "total", "notation"),
    [
        (RollMode.NORMAL, 9, "1d20 + 5"),
        (RollMode.ADVANTAGE, 22, "2d20kh1 + 5"),
        (RollMode.DISADVANTAGE, 9, "2d20kl1 + 5"),
    ],
)
def test_roll_mode_argument(mode: RollMode, total: int, notation: str) -> None:
    result = roll("1d20+5", ScriptedRng([4, 17]), mode=mode)

    assert result.total == total
    assert result.notation == notation
    assert result.mode is mode


@pytest.mark.parametrize(
    ("notation", "total"),
    [
        ("1d20+5 adv", 22),
        ("1к20+5 преимущество", 22),
        ("1d20+5 dis", 9),
        ("1к20+5 помеха", 9),
    ],
)
def test_roll_mode_from_notation(notation: str, total: int) -> None:
    assert roll(notation, ScriptedRng([4, 17])).total == total


def test_mode_argument_overrides_notation() -> None:
    result = roll("1d20 adv", ScriptedRng([4]), mode=RollMode.NORMAL)

    assert result.total == 4
    assert result.notation == "1d20"


def test_advantage_works_for_any_dice() -> None:
    # 2d6 с преимуществом: бросаем три кости, две старшие идут в сумму.
    result = roll("2d6+1", ScriptedRng([2, 6, 4]), mode=RollMode.ADVANTAGE)

    assert result.notation == "3d6kh2 + 1"
    assert result.total == 11


def test_mode_changes_only_the_first_dice_group() -> None:
    result = roll("3 + 1d20 + 1d4", ScriptedRng([8, 15, 2]), mode=RollMode.ADVANTAGE)

    assert result.notation == "3 + 2d20kh1 + 1d4"
    assert result.total == 20


def test_mode_without_dice_changes_nothing() -> None:
    result = roll("5", ScriptedRng([]), mode=RollMode.ADVANTAGE)

    assert result.total == 5
    assert result.notation == "5"


def test_mode_on_a_group_with_keep_is_rejected() -> None:
    with pytest.raises(DiceNotationError) as error:
        roll("4d6kh3", ScriptedRng([]), mode=RollMode.DISADVANTAGE)

    assert error.value.code == "mode_with_keep"


def test_roll_accepts_a_prepared_expression_with_labels() -> None:
    expression = DiceExpression(
        terms=(DiceTerm(count=1, sides=20), ConstantTerm.signed(-1, label="dex_mod"))
    )

    result = roll(expression, ScriptedRng([12]))

    assert result.total == 11
    assert result.terms[1].model_dump() == {
        "kind": "constant",
        "sign": -1,
        "value": 1,
        "label": "dex_mod",
        "subtotal": -1,
    }


def test_same_seed_gives_the_same_roll() -> None:
    notation = "8d6 + 2d20kh1 - 1d4 + 3"

    assert roll(notation, SeededRng(2026)) == roll(notation, SeededRng(2026))
    assert roll(notation, SeededRng(1)) != roll(notation, SeededRng(2))


@pytest.mark.parametrize(
    ("notation", "bounds"),
    [
        ("2d6+3", (5, 15)),
        ("4d6kh3", (3, 18)),
        ("1d20 - 1d4 - 2", (-5, 17)),
        ("1d20+5 adv", (6, 25)),
        ("7", (7, 7)),
    ],
)
def test_totals_stay_within_bounds(notation: str, bounds: tuple[int, int]) -> None:
    expression = parse(notation)
    assert expression.bounds() == bounds

    totals = {roll(expression, SeededRng(seed)).total for seed in range(300)}

    assert min(totals) >= bounds[0]
    assert max(totals) <= bounds[1]
    assert len(totals) > 1 or bounds[0] == bounds[1]


def test_result_survives_json_round_trip() -> None:
    result = roll("2d20kh1 + 5 - 1d4", ScriptedRng([17, 4, 3]))

    restored = RollResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.terms[0].model_dump() == {
        "kind": "dice",
        "sign": 1,
        "notation": "2d20kh1",
        "sides": 20,
        "rolls": ({"value": 17, "kept": True}, {"value": 4, "kept": False}),
        "subtotal": 17,
    }


def test_result_is_immutable() -> None:
    result = roll("1d6", ScriptedRng([3]))

    with pytest.raises(ValueError, match="frozen"):
        result.total = 6  # type: ignore[misc]


def _dice(result: RollResult) -> tuple[DieRoll, ...]:
    """Кости первой группы броска."""
    first = result.terms[0]
    assert first.kind == "dice"
    return first.rolls
