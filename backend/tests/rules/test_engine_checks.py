"""Проверки, спасброски и состязания по набору правил 5e."""

import pytest

from ai_dnd.rules.actions import CheckAction, ContestAction, SaveAction
from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import CheckResolved, ContestResolved, DiceRolled
from ai_dnd.rules.rng import ScriptedRng
from ai_dnd.rules.ruleset import Difficulty, Outcome
from ai_dnd.rules.state import ActiveCondition, Actor, GameState
from ai_dnd.rules.visibility import DM, PUBLIC


def state_of(*actors: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in actors})


def with_condition(actor: Actor, condition: str) -> Actor:
    return actor.with_conditions((*actor.conditions, ActiveCondition(id=condition)))


def check(
    engine: RulesEngine,
    actor: Actor,
    dice: list[int],
    *,
    check: str,
    difficulty: Difficulty | None = None,
    target: int | None = None,
    mode: RollMode = RollMode.NORMAL,
    visibility: str = PUBLIC,
) -> tuple[DiceRolled, CheckResolved]:
    """Проверка с заранее заданными костями: событие броска и итог."""
    action = CheckAction(
        actor_id=actor.id,
        check=check,
        difficulty=difficulty,
        target=target,
        mode=mode,
        visibility=visibility,
    )
    events = engine.resolve(state_of(actor), action, ScriptedRng(dice))
    rolled, resolved = events
    assert isinstance(rolled, DiceRolled)
    assert isinstance(resolved, CheckResolved)
    return rolled, resolved


# --- Проверки ---


def test_stealth_check_succeeds_when_total_reaches_the_difficulty(
    engine: RulesEngine, rogue: Actor
) -> None:
    # Скрытность +7 против «трудно» (20): на кости 13.
    rolled, resolved = check(engine, rogue, [13], check="stealth", difficulty=Difficulty.HARD)

    assert rolled.actor_id == "rogue"
    assert rolled.reason == "check"
    assert str(rolled.roll) == "1d20 + 7 → [13] + 7 = 20"
    assert rolled.roll.terms[1].model_dump()["label"] == "stealth"

    assert resolved == CheckResolved(
        actor_id="rogue",
        kind="check",
        check="stealth",
        outcome=Outcome.SUCCESS,
        total=20,
        natural=13,
        target=20,
        difficulty=Difficulty.HARD,
        mode=RollMode.NORMAL,
    )


def test_stealth_check_fails_one_point_short(engine: RulesEngine, rogue: Actor) -> None:
    _, resolved = check(engine, rogue, [12], check="stealth", difficulty=Difficulty.HARD)

    assert (resolved.outcome, resolved.total, resolved.target) == (Outcome.FAILURE, 19, 20)


@pytest.mark.parametrize(
    ("difficulty", "target"),
    [
        (Difficulty.VERY_EASY, 5),
        (Difficulty.EASY, 10),
        (Difficulty.MEDIUM, 15),
        (Difficulty.HARD, 20),
        (Difficulty.VERY_HARD, 25),
        (Difficulty.NEARLY_IMPOSSIBLE, 30),
    ],
)
def test_difficulty_level_becomes_a_number(
    engine: RulesEngine, rogue: Actor, difficulty: Difficulty, target: int
) -> None:
    _, resolved = check(engine, rogue, [10], check="stealth", difficulty=difficulty)

    assert resolved.target == target
    # На кости 10, бонус +7.
    assert resolved.outcome is (Outcome.SUCCESS if target <= 17 else Outcome.FAILURE)


def test_exact_number_from_scenario_wins_over_the_level(engine: RulesEngine, rogue: Actor) -> None:
    _, resolved = check(
        engine, rogue, [7], check="perception", difficulty=Difficulty.HARD, target=14
    )

    # Восприятие +3: 10 против 14, а не против 20.
    assert (resolved.total, resolved.target, resolved.outcome) == (10, 14, Outcome.FAILURE)


def test_check_needs_a_difficulty(engine: RulesEngine, rogue: Actor) -> None:
    with pytest.raises(ActionError) as error:
        engine.resolve(
            state_of(rogue), CheckAction(actor_id="rogue", check="stealth"), ScriptedRng([])
        )

    assert error.value.code == "missing_difficulty"


def test_natural_twenty_is_not_an_automatic_success_on_checks(
    engine: RulesEngine, rogue: Actor
) -> None:
    # В 5e критические исходы есть только у атак.
    _, resolved = check(
        engine, rogue, [20], check="stealth", difficulty=Difficulty.NEARLY_IMPOSSIBLE
    )
    assert (resolved.total, resolved.outcome) == (27, Outcome.FAILURE)

    _, resolved = check(engine, rogue, [1], check="stealth", difficulty=Difficulty.VERY_EASY)
    assert (resolved.total, resolved.outcome) == (8, Outcome.SUCCESS)


def test_negative_bonus_is_subtracted(engine: RulesEngine, fighter: Actor) -> None:
    rolled, resolved = check(engine, fighter, [11], check="deception", target=10)

    assert str(rolled.roll) == "1d20 - 1 → [11] - 1 = 10"
    assert resolved.outcome is Outcome.SUCCESS


def test_skill_without_own_bonus_uses_its_ability(engine: RulesEngine, goblin: Actor) -> None:
    # У гоблина записана только Скрытность; Атлетика считается по Силе (−1).
    rolled, _ = check(engine, goblin, [10], check="athletics", target=10)
    assert str(rolled.roll) == "1d20 - 1 → [10] - 1 = 9"

    rolled, _ = check(engine, goblin, [10], check="stealth", target=10)
    assert rolled.roll.total == 16


@pytest.mark.parametrize(
    ("mode", "notation", "natural"),
    [
        (RollMode.ADVANTAGE, "2d20kh1 + 7", 17),
        (RollMode.DISADVANTAGE, "2d20kl1 + 7", 4),
    ],
)
def test_advantage_and_disadvantage_from_circumstances(
    engine: RulesEngine, rogue: Actor, mode: RollMode, notation: str, natural: int
) -> None:
    rolled, resolved = check(engine, rogue, [4, 17], check="stealth", target=15, mode=mode)

    assert rolled.roll.notation == notation
    assert (resolved.natural, resolved.mode) == (natural, mode)


def test_hidden_check_stays_hidden(engine: RulesEngine, rogue: Actor) -> None:
    rolled, resolved = check(engine, rogue, [5], check="perception", target=12, visibility=DM)

    assert (rolled.visibility, resolved.visibility) == (DM, DM)


# --- Состояния ---


def test_poisoned_gives_disadvantage_on_checks(engine: RulesEngine, rogue: Actor) -> None:
    rolled, resolved = check(
        engine, with_condition(rogue, "poisoned"), [18, 6], check="stealth", target=15
    )

    assert rolled.roll.notation == "2d20kl1 + 7"
    assert (resolved.total, resolved.mode, resolved.outcome) == (
        13,
        RollMode.DISADVANTAGE,
        Outcome.FAILURE,
    )


def test_advantage_and_disadvantage_cancel_out(engine: RulesEngine, rogue: Actor) -> None:
    poisoned = with_condition(rogue, "poisoned")

    rolled, resolved = check(
        engine, poisoned, [18], check="stealth", target=15, mode=RollMode.ADVANTAGE
    )

    assert rolled.roll.notation == "1d20 + 7"
    assert resolved.mode is RollMode.NORMAL


def test_condition_does_not_touch_other_rolls(engine: RulesEngine, rogue: Actor) -> None:
    # «Опутан» мешает спасброскам Ловкости, но не проверкам.
    rolled, _ = check(engine, with_condition(rogue, "restrained"), [9], check="stealth", target=15)

    assert rolled.roll.notation == "1d20 + 7"


@pytest.mark.parametrize("condition", ["unconscious", "paralyzed", "stunned", "incapacitated"])
def test_incapacitated_actor_cannot_make_checks(
    engine: RulesEngine, rogue: Actor, condition: str
) -> None:
    state = state_of(with_condition(rogue, condition))

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state, CheckAction(actor_id="rogue", check="stealth", target=10), ScriptedRng([])
        )

    assert error.value.code == "actor_incapacitated"
    assert error.value.params == {"actor_id": "rogue", "condition": condition}


# --- Спасброски ---


def test_saving_throw(engine: RulesEngine, cleric: Actor) -> None:
    events = engine.resolve(
        state_of(cleric),
        SaveAction(actor_id="cleric", save="wis", difficulty=Difficulty.MEDIUM),
        ScriptedRng([10]),
    )

    rolled, resolved = events
    assert isinstance(rolled, DiceRolled)
    assert rolled.reason == "save"
    assert str(rolled.roll) == "1d20 + 5 → [10] + 5 = 15"
    assert resolved == CheckResolved(
        actor_id="cleric",
        kind="save",
        check="wis",
        outcome=Outcome.SUCCESS,
        total=15,
        natural=10,
        target=15,
        difficulty=Difficulty.MEDIUM,
        mode=RollMode.NORMAL,
    )


def test_unconscious_actor_fails_dexterity_save_without_a_roll(
    engine: RulesEngine, rogue: Actor
) -> None:
    rng = ScriptedRng([20])

    events = engine.resolve(
        state_of(with_condition(rogue, "unconscious")),
        SaveAction(actor_id="rogue", save="dex", target=5),
        rng,
    )

    assert events == [
        CheckResolved(
            actor_id="rogue",
            kind="save",
            check="dex",
            outcome=Outcome.FAILURE,
            total=None,
            natural=None,
            target=5,
            difficulty=None,
            mode=RollMode.NORMAL,
            auto_failed_by="unconscious",
        )
    ]
    # Кость не тронута: броска не было.
    assert rng.remaining == 1


def test_unconscious_actor_still_rolls_other_saves(engine: RulesEngine, rogue: Actor) -> None:
    events = engine.resolve(
        state_of(with_condition(rogue, "unconscious")),
        SaveAction(actor_id="rogue", save="wis", target=10),
        ScriptedRng([12]),
    )

    resolved = events[-1]
    assert isinstance(resolved, CheckResolved)
    assert (resolved.total, resolved.outcome, resolved.auto_failed_by) == (
        13,
        Outcome.SUCCESS,
        None,
    )


def test_restrained_gives_disadvantage_on_dexterity_saves(
    engine: RulesEngine, rogue: Actor
) -> None:
    restrained = state_of(with_condition(rogue, "restrained"))

    dex = engine.resolve(
        restrained, SaveAction(actor_id="rogue", save="dex", target=10), ScriptedRng([15, 3])
    )
    wis = engine.resolve(
        restrained, SaveAction(actor_id="rogue", save="wis", target=10), ScriptedRng([15])
    )

    assert isinstance(dex[0], DiceRolled)
    assert isinstance(wis[0], DiceRolled)
    assert dex[0].roll.notation == "2d20kl1 + 5"
    assert wis[0].roll.notation == "1d20 + 1"


# --- Состязания ---


def test_contest_is_won_by_the_higher_total(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    # Атлетика воина +5 против Атлетики гоблина −1.
    events = engine.resolve(
        state_of(fighter, goblin),
        ContestAction(
            actor_id="fighter", check="athletics", opponent_id="goblin", opponent_check="athletics"
        ),
        ScriptedRng([8, 15]),
    )

    first, second, resolved = events
    assert isinstance(first, DiceRolled)
    assert isinstance(second, DiceRolled)
    assert (first.actor_id, first.reason, first.roll.total) == ("fighter", "contest", 13)
    assert (second.actor_id, second.roll.total) == ("goblin", 14)
    assert resolved == ContestResolved(
        actor_id="fighter",
        check="athletics",
        total=13,
        opponent_id="goblin",
        opponent_check="athletics",
        opponent_total=14,
        winner_id="goblin",
    )


def test_contest_can_use_different_checks_and_end_in_a_tie(
    engine: RulesEngine, rogue: Actor, goblin: Actor
) -> None:
    # Скрытность плутовки +7 против Восприятия гоблина (по Мудрости, −1).
    events = engine.resolve(
        state_of(rogue, goblin),
        ContestAction(
            actor_id="rogue", check="stealth", opponent_id="goblin", opponent_check="perception"
        ),
        ScriptedRng([5, 13]),
    )

    resolved = events[-1]
    assert isinstance(resolved, ContestResolved)
    assert (resolved.total, resolved.opponent_total, resolved.winner_id) == (12, 12, None)


def test_contest_respects_modes_of_both_sides(
    engine: RulesEngine, rogue: Actor, goblin: Actor
) -> None:
    events = engine.resolve(
        state_of(with_condition(rogue, "poisoned"), goblin),
        ContestAction(
            actor_id="rogue",
            check="stealth",
            opponent_id="goblin",
            opponent_check="perception",
            opponent_mode=RollMode.ADVANTAGE,
        ),
        ScriptedRng([12, 3, 2, 19]),
    )

    first, second, resolved = events
    assert isinstance(first, DiceRolled)
    assert isinstance(second, DiceRolled)
    assert isinstance(resolved, ContestResolved)
    assert first.roll.notation == "2d20kl1 + 7"
    assert second.roll.notation == "2d20kh1 - 1"
    assert (resolved.total, resolved.opponent_total, resolved.winner_id) == (10, 18, "goblin")


# --- Чего сделать нельзя ---


@pytest.mark.parametrize(
    ("action", "code"),
    [
        (CheckAction(actor_id="ghost", check="stealth", target=10), "unknown_actor"),
        (CheckAction(actor_id="rogue", check="swimming", target=10), "unknown_check"),
        (SaveAction(actor_id="rogue", save="luck", target=10), "unknown_save"),
        (SaveAction(actor_id="rogue", save="stealth", target=10), "unknown_save"),
        (
            ContestAction(
                actor_id="rogue", check="stealth", opponent_id="ghost", opponent_check="perception"
            ),
            "unknown_actor",
        ),
    ],
)
def test_impossible_actions_are_refused(
    engine: RulesEngine, rogue: Actor, action: CheckAction | SaveAction | ContestAction, code: str
) -> None:
    rng = ScriptedRng([10, 10])

    with pytest.raises(ActionError) as error:
        engine.resolve(state_of(rogue), action, rng)

    assert error.value.code == code
    assert rng.remaining == 2


def test_actor_without_a_bonus_cannot_make_the_check(engine: RulesEngine) -> None:
    statue = Actor(id="statue", name="Статуя")

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state_of(statue),
            CheckAction(actor_id="statue", check="stealth", target=10),
            ScriptedRng([]),
        )
    assert error.value.code == "check_not_available"

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state_of(statue), SaveAction(actor_id="statue", save="dex", target=10), ScriptedRng([])
        )
    assert error.value.code == "save_not_available"


def test_dead_actor_cannot_act(engine: RulesEngine, goblin: Actor) -> None:
    dead = goblin.model_copy(update={"dead": True})

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state_of(dead),
            CheckAction(actor_id="goblin", check="stealth", target=10),
            ScriptedRng([]),
        )

    assert error.value.code == "actor_dead"


def test_resolve_does_not_change_the_state(engine: RulesEngine, rogue: Actor) -> None:
    state = state_of(rogue)
    before = state.model_dump_json()

    engine.resolve(
        state, CheckAction(actor_id="rogue", check="stealth", target=10), ScriptedRng([10])
    )

    assert state.model_dump_json() == before
