"""Система, не похожая на 5e: 2d6 + бонус, исход по диапазонам, боя нет.

Движок тот же, отличается только набор правил — он целиком задан данными.
"""

import pytest

from ai_dnd.rules.actions import AttackAction, CheckAction, ContestAction
from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import CheckResolved, ContestResolved, DiceRolled
from ai_dnd.rules.rng import ScriptedRng
from ai_dnd.rules.ruleset import Difficulty, Outcome, Ruleset
from ai_dnd.rules.state import ActiveCondition, Actor, GameState


@pytest.fixture
def engine() -> RulesEngine:
    ruleset = Ruleset.model_validate(
        {
            "id": "two-dice",
            "version": 1,
            "name": "Две кости",
            "resolution": {
                "type": "roll_bands",
                "dice": "2d6",
                "bands": [
                    {"min": 7, "outcome": "partial_success"},
                    {"min": 10, "outcome": "success"},
                    {"min": 12, "outcome": "critical_success"},
                ],
            },
            # Сложность здесь — поправка к броску, а не число, которое нужно выбросить.
            "difficulty": {
                "very_easy": 2,
                "easy": 1,
                "medium": 0,
                "hard": -1,
                "very_hard": -2,
                "nearly_impossible": -3,
            },
            "checks": {"cool": {"label": "Хладнокровие"}, "sharp": {"label": "Смекалка"}},
            "conditions": {
                "shaken": {
                    "label": "Потрясён",
                    "effects": [{"applies_to": "checks", "bonus": -1}],
                },
                "frozen": {
                    "label": "Оцепенел",
                    "effects": [{"applies_to": "checks", "only": ["cool"], "auto_fail": True}],
                },
            },
            "combat": {"enabled": False},
        }
    )
    return RulesEngine(ruleset)


@pytest.fixture
def driver() -> Actor:
    return Actor(id="driver", name="Водитель", kind="character", checks={"cool": 2, "sharp": 0})


@pytest.fixture
def rival() -> Actor:
    return Actor(id="rival", name="Соперник", checks={"cool": 1, "sharp": 1})


def state_of(*actors: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in actors})


def resolve_check(
    engine: RulesEngine, actor: Actor, dice: list[int], difficulty: Difficulty | None = None
) -> tuple[DiceRolled, CheckResolved]:
    action = CheckAction(actor_id=actor.id, check="cool", difficulty=difficulty)
    rolled, resolved = engine.resolve(state_of(actor), action, ScriptedRng(dice))
    assert isinstance(rolled, DiceRolled)
    assert isinstance(resolved, CheckResolved)
    return rolled, resolved


@pytest.mark.parametrize(
    ("dice", "total", "outcome"),
    [
        ([1, 1], 4, Outcome.FAILURE),
        ([2, 2], 6, Outcome.FAILURE),
        ([2, 3], 7, Outcome.PARTIAL_SUCCESS),
        ([3, 4], 9, Outcome.PARTIAL_SUCCESS),
        ([4, 4], 10, Outcome.SUCCESS),
        ([4, 5], 11, Outcome.SUCCESS),
        ([5, 5], 12, Outcome.CRITICAL_SUCCESS),
        ([6, 6], 14, Outcome.CRITICAL_SUCCESS),
    ],
)
def test_outcome_depends_on_the_band(
    engine: RulesEngine, driver: Actor, dice: list[int], total: int, outcome: Outcome
) -> None:
    rolled, resolved = resolve_check(engine, driver, dice)

    assert rolled.roll.notation == "2d6 + 2"
    assert (resolved.total, resolved.outcome) == (total, outcome)
    # Числа-цели в такой системе нет.
    assert resolved.target is None


def test_difficulty_shifts_the_roll(engine: RulesEngine, driver: Actor) -> None:
    rolled, resolved = resolve_check(engine, driver, [4, 4], Difficulty.HARD)

    assert str(rolled.roll) == "2d6 + 2 - 1 → [4, 4] + 2 - 1 = 9"
    assert rolled.roll.terms[2].model_dump()["label"] == "difficulty"
    assert (resolved.outcome, resolved.difficulty) == (Outcome.PARTIAL_SUCCESS, Difficulty.HARD)

    rolled, resolved = resolve_check(engine, driver, [4, 4], Difficulty.VERY_EASY)
    assert (rolled.roll.total, resolved.outcome) == (12, Outcome.CRITICAL_SUCCESS)


def test_medium_difficulty_adds_nothing_to_the_roll(engine: RulesEngine, driver: Actor) -> None:
    rolled, _ = resolve_check(engine, driver, [3, 3], Difficulty.MEDIUM)

    assert rolled.roll.notation == "2d6 + 2"


def test_exact_target_makes_no_sense_here(engine: RulesEngine, driver: Actor) -> None:
    action = CheckAction(actor_id="driver", check="cool", target=15)

    with pytest.raises(ActionError) as error:
        engine.resolve(state_of(driver), action, ScriptedRng([]))

    assert error.value.code == "target_not_supported"


def test_condition_can_add_a_flat_modifier(engine: RulesEngine, driver: Actor) -> None:
    shaken = driver.with_conditions((ActiveCondition(id="shaken"),))

    rolled, resolved = resolve_check(engine, shaken, [4, 4])

    assert str(rolled.roll) == "2d6 + 2 - 1 → [4, 4] + 2 - 1 = 9"
    assert rolled.roll.terms[2].model_dump()["label"] == "shaken"
    assert resolved.outcome is Outcome.PARTIAL_SUCCESS


def test_advantage_adds_a_die_to_any_dice(engine: RulesEngine, driver: Actor) -> None:
    action = CheckAction(actor_id="driver", check="cool", mode=RollMode.ADVANTAGE)

    rolled, resolved = engine.resolve(state_of(driver), action, ScriptedRng([1, 5, 4]))

    assert isinstance(rolled, DiceRolled)
    assert isinstance(resolved, CheckResolved)
    assert str(rolled.roll) == "3d6kh2 + 2 → [(1), 5, 4] + 2 = 11"
    assert resolved.outcome is Outcome.SUCCESS


def test_condition_can_fail_a_check_without_a_roll(engine: RulesEngine, driver: Actor) -> None:
    frozen = driver.with_conditions((ActiveCondition(id="frozen"),))

    events = engine.resolve(
        state_of(frozen), CheckAction(actor_id="driver", check="cool"), ScriptedRng([])
    )

    (resolved,) = events
    assert isinstance(resolved, CheckResolved)
    assert (resolved.outcome, resolved.total, resolved.auto_failed_by) == (
        Outcome.FAILURE,
        None,
        "frozen",
    )


def test_contest_is_lost_by_the_side_that_fails_automatically(
    engine: RulesEngine, driver: Actor, rival: Actor
) -> None:
    frozen = driver.with_conditions((ActiveCondition(id="frozen"),))
    action = ContestAction(
        actor_id="driver", check="cool", opponent_id="rival", opponent_check="sharp"
    )

    events = engine.resolve(state_of(frozen, rival), action, ScriptedRng([1, 1]))

    rolled, resolved = events
    assert isinstance(rolled, DiceRolled)
    assert rolled.actor_id == "rival"
    assert resolved == ContestResolved(
        actor_id="driver",
        check="cool",
        total=None,
        opponent_id="rival",
        opponent_check="sharp",
        opponent_total=3,
        winner_id="rival",
    )


def test_no_attacks_when_combat_is_disabled(
    engine: RulesEngine, driver: Actor, rival: Actor
) -> None:
    action = AttackAction(attacker_id="driver", target_id="rival", attack="punch")

    with pytest.raises(ActionError) as error:
        engine.resolve(state_of(driver, rival), action, ScriptedRng([]))

    assert error.value.code == "combat_disabled"
