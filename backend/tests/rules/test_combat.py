"""Бой: инициатива, очередь ходов, раунды, действия за ход, бегство, конец боя."""

import pytest

from ai_dnd.rules.actions import (
    Action,
    AttackAction,
    DamageAction,
    EndCombatAction,
    EndTurnAction,
    FleeAction,
    StartCombatAction,
    UseTurnSlotAction,
)
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import (
    ActorDied,
    ActorFled,
    CombatEnded,
    CombatStarted,
    ConditionApplied,
    ConditionRemoved,
    DiceRolled,
    Event,
    RoundStarted,
    TurnEnded,
    TurnSlotUsed,
    TurnStarted,
)
from ai_dnd.rules.rng import ScriptedRng
from ai_dnd.rules.ruleset import Ruleset, TurnBudget
from ai_dnd.rules.state import ActiveCondition, Actor, CombatState, GameState

FULL_TURN = TurnBudget(action=1, bonus_action=1, movement=1)
NO_DICE = ScriptedRng([])


def state_of(*actors: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in actors})


def copy_of(actor: Actor, actor_id: str) -> Actor:
    return actor.model_copy(update={"id": actor_id})


def types_of(events: list[Event]) -> list[str]:
    return [event.type for event in events]


def in_combat(state: GameState) -> CombatState:
    assert state.combat is not None
    return state.combat


def begin(
    engine: RulesEngine, state: GameState, sides: dict[str, tuple[str, ...]], dice: list[int]
) -> GameState:
    """Начать бой с заданными бросками инициативы (по одному на участника, по порядку сторон)."""
    _, state = engine.run(state, StartCombatAction(sides=sides), ScriptedRng(dice))
    return state


@pytest.fixture
def skirmish(engine: RulesEngine, fighter: Actor, rogue: Actor, goblin: Actor) -> GameState:
    """Воин и плутовка против гоблина. Очередь: гоблин (12), воин (11), плутовка (8)."""
    return begin(
        engine,
        state_of(fighter, rogue, goblin),
        {"party": ("fighter", "rogue"), "enemies": ("goblin",)},
        [10, 5, 10],
    )


@pytest.fixture
def ambush(engine: RulesEngine, fighter: Actor, goblin: Actor) -> GameState:
    """Воин против двух гоблинов. Очередь: воин (21), первый гоблин (12), второй (7)."""
    state = state_of(fighter, copy_of(goblin, "goblin_1"), copy_of(goblin, "goblin_2"))
    return begin(
        engine, state, {"party": ("fighter",), "enemies": ("goblin_1", "goblin_2")}, [20, 10, 5]
    )


# --- Инициатива ---


def test_combat_starts_with_initiative_rolls(
    engine: RulesEngine, fighter: Actor, rogue: Actor, goblin: Actor
) -> None:
    action = StartCombatAction(sides={"party": ("fighter", "rogue"), "enemies": ("goblin",)})

    events, after = engine.run(state_of(fighter, rogue, goblin), action, ScriptedRng([10, 5, 10]))

    rolls = events[:3]
    assert all(isinstance(event, DiceRolled) and event.reason == "initiative" for event in rolls)
    assert [str(event.roll) for event in rolls if isinstance(event, DiceRolled)] == [
        "1d20 + 1 → [10] + 1 = 11",
        "1d20 + 3 → [5] + 3 = 8",
        "1d20 + 2 → [10] + 2 = 12",
    ]
    assert events[3:] == [
        CombatStarted(
            sides={"fighter": "party", "rogue": "party", "goblin": "enemies"},
            order=("goblin", "fighter", "rogue"),
            initiatives={"fighter": 11, "rogue": 8, "goblin": 12},
        ),
        RoundStarted(round=1),
        TurnStarted(actor_id="goblin", round=1, budget=FULL_TURN),
    ]
    assert after.combat == CombatState(
        round=1,
        order=("goblin", "fighter", "rogue"),
        sides={"fighter": "party", "rogue": "party", "goblin": "enemies"},
        initiatives={"fighter": 11, "rogue": 8, "goblin": 12},
        current="goblin",
        remaining=FULL_TURN,
    )


def test_initiative_ties_go_to_the_higher_bonus_then_to_the_first_named(
    engine: RulesEngine, fighter: Actor, rogue: Actor, goblin: Actor
) -> None:
    twin = copy_of(goblin, "twin")
    state = state_of(fighter, rogue, goblin, twin)
    sides: dict[str, tuple[str, ...]] = {
        "party": ("fighter", "rogue"),
        "enemies": ("goblin", "twin"),
    }

    # У всех выходит 13: бонусы +1, +3, +2, +2.
    after = begin(engine, state, sides, [12, 10, 11, 11])

    assert in_combat(after).order == ("rogue", "goblin", "twin", "fighter")
    assert set(in_combat(after).initiatives.values()) == {13}


@pytest.mark.parametrize(
    ("sides", "code"),
    [
        ({"party": ("fighter",)}, "not_enough_sides"),
        ({"party": ("fighter",), "enemies": ()}, "not_enough_sides"),
        ({"party": ("fighter",), "enemies": ("ghost",)}, "unknown_actor"),
        ({"party": ("fighter", "goblin"), "enemies": ("goblin",)}, "actor_on_two_sides"),
        ({"party": ("fighter",), "enemies": ("corpse",)}, "actor_dead"),
    ],
)
def test_impossible_combat_is_not_started(
    engine: RulesEngine,
    fighter: Actor,
    goblin: Actor,
    sides: dict[str, tuple[str, ...]],
    code: str,
) -> None:
    corpse = copy_of(goblin, "corpse").model_copy(update={"dead": True})
    rng = ScriptedRng([10, 10])

    with pytest.raises(ActionError) as error:
        engine.resolve(state_of(fighter, goblin, corpse), StartCombatAction(sides=sides), rng)

    assert error.value.code == code
    # Инициативу не бросали.
    assert rng.remaining == 2


def test_combat_cannot_be_started_twice(engine: RulesEngine, skirmish: GameState) -> None:
    action = StartCombatAction(sides={"party": ("fighter",), "enemies": ("goblin",)})

    with pytest.raises(ActionError) as error:
        engine.resolve(skirmish, action, NO_DICE)

    assert error.value.code == "combat_already_started"


def test_combat_needs_a_ruleset_with_combat(
    srd5_ruleset: Ruleset, fighter: Actor, goblin: Actor
) -> None:
    data = srd5_ruleset.model_dump(mode="json")
    data["combat"]["enabled"] = False
    peaceful = RulesEngine(Ruleset.model_validate(data))
    action = StartCombatAction(sides={"party": ("fighter",), "enemies": ("goblin",)})

    with pytest.raises(ActionError) as error:
        peaceful.resolve(state_of(fighter, goblin), action, NO_DICE)

    assert error.value.code == "combat_disabled"


# --- Очередь ходов и раунды ---


def test_turns_pass_in_initiative_order_and_rounds_wrap(
    engine: RulesEngine, skirmish: GameState
) -> None:
    events, state = engine.run(skirmish, EndTurnAction(actor_id="goblin"), NO_DICE)
    assert events == [
        TurnEnded(actor_id="goblin"),
        TurnStarted(actor_id="fighter", round=1, budget=FULL_TURN),
    ]

    events, state = engine.run(state, EndTurnAction(actor_id="fighter"), NO_DICE)
    assert events[-1] == TurnStarted(actor_id="rogue", round=1, budget=FULL_TURN)

    events, state = engine.run(state, EndTurnAction(actor_id="rogue"), NO_DICE)
    assert events == [
        TurnEnded(actor_id="rogue"),
        RoundStarted(round=2),
        TurnStarted(actor_id="goblin", round=2, budget=FULL_TURN),
    ]
    assert (in_combat(state).round, in_combat(state).current) == (2, "goblin")


def test_only_the_current_actor_can_end_the_turn(engine: RulesEngine, skirmish: GameState) -> None:
    with pytest.raises(ActionError) as error:
        engine.resolve(skirmish, EndTurnAction(actor_id="rogue"), NO_DICE)

    assert error.value.code == "not_your_turn"
    assert error.value.params == {"actor_id": "rogue", "current": "goblin"}


def test_dead_actors_are_skipped(engine: RulesEngine, ambush: GameState) -> None:
    # Ведущий убивает первого гоблина ловушкой, пока ходит воин.
    events, state = engine.run(ambush, DamageAction(target_id="goblin_1", damage="20"), NO_DICE)
    assert events[-1] == ActorDied(actor_id="goblin_1")
    assert in_combat(state).order == ("fighter", "goblin_1", "goblin_2")

    events, state = engine.run(state, EndTurnAction(actor_id="fighter"), NO_DICE)

    assert events[-1] == TurnStarted(actor_id="goblin_2", round=1, budget=FULL_TURN)


def test_turn_actions_need_a_combat(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    state = state_of(fighter, goblin)
    actions: list[Action] = [
        EndTurnAction(actor_id="fighter"),
        FleeAction(actor_id="fighter"),
        UseTurnSlotAction(actor_id="fighter", slot="action"),
        EndCombatAction(),
    ]

    for action in actions:
        with pytest.raises(ActionError) as error:
            engine.resolve(state, action, NO_DICE)
        assert error.value.code == "no_combat"


# --- Действия за ход ---


def test_attack_costs_the_action(engine: RulesEngine, ambush: GameState) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1")

    events, state = engine.run(ambush, attack, ScriptedRng([5]))

    assert events[0] == TurnSlotUsed(actor_id="fighter", slot="action")
    assert types_of(events) == ["TurnSlotUsed", "DiceRolled", "AttackResolved"]
    assert in_combat(state).remaining == TurnBudget(action=0, bonus_action=1, movement=1)

    with pytest.raises(ActionError) as error:
        engine.resolve(state, attack, ScriptedRng([5]))
    assert error.value.code == "turn_slot_spent"
    assert error.value.params == {"actor_id": "fighter", "slot": "action"}


def test_second_attack_can_be_paid_with_the_bonus_action(
    engine: RulesEngine, ambush: GameState
) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1")
    _, state = engine.run(ambush, attack, ScriptedRng([5]))

    off_hand = attack.model_copy(update={"slot": "bonus_action"})
    events, state = engine.run(state, off_hand, ScriptedRng([5]))

    assert events[0] == TurnSlotUsed(actor_id="fighter", slot="bonus_action")
    assert in_combat(state).remaining == TurnBudget(action=0, bonus_action=0, movement=1)


def test_attack_is_possible_only_on_your_turn(engine: RulesEngine, ambush: GameState) -> None:
    attack = AttackAction(attacker_id="goblin_1", target_id="fighter", attack="scimitar")
    rng = ScriptedRng([15, 3])

    with pytest.raises(ActionError) as error:
        engine.resolve(ambush, attack, rng)

    assert error.value.code == "not_your_turn"
    assert rng.remaining == 2


def test_free_attack_ignores_the_turn_order(engine: RulesEngine, ambush: GameState) -> None:
    # Например, атака вне очереди по решению ведущего.
    attack = AttackAction(
        attacker_id="goblin_1", target_id="fighter", attack="scimitar", slot="free"
    )

    events, state = engine.run(ambush, attack, ScriptedRng([15, 3]))

    assert types_of(events) == ["DiceRolled", "AttackResolved", "DiceRolled", "DamageDealt"]
    assert in_combat(state).remaining == FULL_TURN


def test_new_turn_gets_a_fresh_budget(engine: RulesEngine, ambush: GameState) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1")
    _, state = engine.run(ambush, attack, ScriptedRng([5]))

    _, state = engine.run(state, EndTurnAction(actor_id="fighter"), NO_DICE)

    assert (in_combat(state).current, in_combat(state).remaining) == ("goblin_1", FULL_TURN)


def test_part_of_the_turn_can_be_spent_on_something_else(
    engine: RulesEngine, ambush: GameState
) -> None:
    second_wind = UseTurnSlotAction(actor_id="fighter", slot="bonus_action", label="Второе дыхание")

    events, state = engine.run(ambush, second_wind, NO_DICE)

    assert events == [TurnSlotUsed(actor_id="fighter", slot="bonus_action", label="Второе дыхание")]
    assert in_combat(state).remaining == TurnBudget(action=1, bonus_action=0, movement=1)

    with pytest.raises(ActionError) as error:
        engine.resolve(state, second_wind, NO_DICE)
    assert error.value.code == "turn_slot_spent"

    with pytest.raises(ActionError) as error:
        engine.resolve(state, UseTurnSlotAction(actor_id="goblin_1", slot="action"), NO_DICE)
    assert error.value.code == "not_your_turn"


def test_attack_outside_combat_is_not_limited(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin", attack="attack_1")

    events = engine.resolve(state_of(fighter, goblin), attack, ScriptedRng([5]))

    assert types_of(events) == ["DiceRolled", "AttackResolved"]


# --- Состояния в бою ---


def test_timed_conditions_tick_at_the_end_of_own_turn(
    engine: RulesEngine, fighter: Actor, rogue: Actor, goblin: Actor
) -> None:
    shaken = rogue.with_conditions(
        (ActiveCondition(id="frightened", rounds=1), ActiveCondition(id="poisoned", rounds=3))
    )
    slowed = goblin.with_conditions((ActiveCondition(id="prone", rounds=1),))
    state = begin(
        engine,
        state_of(fighter, shaken, slowed),
        {"party": ("fighter", "rogue"), "enemies": ("goblin",)},
        [1, 15, 5],
    )
    assert in_combat(state).order == ("rogue", "goblin", "fighter")

    events, state = engine.run(state, EndTurnAction(actor_id="rogue"), NO_DICE)

    assert events == [
        TurnEnded(actor_id="rogue"),
        ConditionRemoved(actor_id="rogue", condition="frightened", reason="expired"),
        TurnStarted(actor_id="goblin", round=1, budget=FULL_TURN),
    ]
    assert state.actor("rogue").conditions == (ActiveCondition(id="poisoned", rounds=2),)
    # Чужие состояния от хода плутовки не убывают.
    assert state.actor("goblin").conditions == (ActiveCondition(id="prone", rounds=1),)


def test_incapacitated_actor_can_only_end_the_turn(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    stunned = fighter.with_conditions((ActiveCondition(id="stunned", rounds=1),))
    state = begin(
        engine, state_of(stunned, goblin), {"party": ("fighter",), "enemies": ("goblin",)}, [20, 1]
    )

    assert engine.available_actions(state, "fighter") == [EndTurnAction(actor_id="fighter")]
    with pytest.raises(ActionError) as error:
        engine.resolve(state, FleeAction(actor_id="fighter"), NO_DICE)
    assert error.value.code == "actor_incapacitated"

    _, state = engine.run(state, EndTurnAction(actor_id="fighter"), NO_DICE)
    assert state.actor("fighter").conditions == ()


# --- Бегство ---


def test_fleeing_costs_action_and_movement_and_ends_the_turn(
    engine: RulesEngine, ambush: GameState
) -> None:
    _, state = engine.run(ambush, EndTurnAction(actor_id="fighter"), NO_DICE)

    events, state = engine.run(state, FleeAction(actor_id="goblin_1"), NO_DICE)

    assert events == [
        TurnSlotUsed(actor_id="goblin_1", slot="action"),
        TurnSlotUsed(actor_id="goblin_1", slot="movement"),
        ActorFled(actor_id="goblin_1"),
        TurnEnded(actor_id="goblin_1"),
        TurnStarted(actor_id="goblin_2", round=1, budget=FULL_TURN),
    ]
    assert in_combat(state).fled == ("goblin_1",)
    assert state.actor("goblin_1").dead is False


def test_fled_actor_is_out_of_the_fight(engine: RulesEngine, ambush: GameState) -> None:
    _, state = engine.run(ambush, EndTurnAction(actor_id="fighter"), NO_DICE)
    _, state = engine.run(state, FleeAction(actor_id="goblin_1"), NO_DICE)
    _, state = engine.run(state, EndTurnAction(actor_id="goblin_2"), NO_DICE)

    # Новый раунд: ход воина, сбежавший гоблин больше не цель и не ходит.
    assert (in_combat(state).round, in_combat(state).current) == (2, "fighter")
    targets = {
        action.target_id
        for action in engine.available_actions(state, "fighter")
        if isinstance(action, AttackAction)
    }
    assert targets == {"goblin_2"}
    with pytest.raises(ActionError) as error:
        engine.resolve(
            state,
            AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1"),
            ScriptedRng([10]),
        )
    assert error.value.code == "actor_fled"

    events, state = engine.run(state, EndTurnAction(actor_id="fighter"), NO_DICE)
    assert events[-1] == TurnStarted(actor_id="goblin_2", round=2, budget=FULL_TURN)


def test_cannot_flee_after_acting(engine: RulesEngine, ambush: GameState) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1")
    _, state = engine.run(ambush, attack, ScriptedRng([5]))

    with pytest.raises(ActionError) as error:
        engine.resolve(state, FleeAction(actor_id="fighter"), NO_DICE)

    assert error.value.code == "turn_slot_spent"


# --- Конец боя ---


def test_combat_ends_when_the_last_enemy_dies(engine: RulesEngine, skirmish: GameState) -> None:
    _, state = engine.run(skirmish, EndTurnAction(actor_id="goblin"), NO_DICE)
    attack = AttackAction(attacker_id="fighter", target_id="goblin", attack="attack_1")

    events, state = engine.run(state, attack, ScriptedRng([15, 6]))

    assert events[-2:] == [ActorDied(actor_id="goblin"), CombatEnded(winner="party")]
    assert state.combat is None
    assert state.actor("goblin").dead is True


def test_combat_ends_when_the_last_enemy_flees(engine: RulesEngine, skirmish: GameState) -> None:
    events, state = engine.run(skirmish, FleeAction(actor_id="goblin"), NO_DICE)

    assert events == [
        TurnSlotUsed(actor_id="goblin", slot="action"),
        TurnSlotUsed(actor_id="goblin", slot="movement"),
        ActorFled(actor_id="goblin"),
        CombatEnded(winner="party"),
    ]
    assert state.combat is None


def test_combat_ends_when_the_whole_party_is_down(
    engine: RulesEngine, rogue: Actor, goblin: Actor
) -> None:
    state = begin(
        engine,
        state_of(rogue.with_resource("hp", 3), goblin),
        {"party": ("rogue",), "enemies": ("goblin",)},
        [1, 20],
    )
    attack = AttackAction(attacker_id="goblin", target_id="rogue", attack="scimitar")

    events, state = engine.run(state, attack, ScriptedRng([15, 4]))

    assert events[-2:] == [
        ConditionApplied(actor_id="rogue", condition="unconscious", rounds=None),
        CombatEnded(winner="enemies"),
    ]
    assert state.combat is None
    # Плутовка без сознания, но жива.
    assert state.actor("rogue").dead is False


def test_combat_can_be_stopped_without_a_winner(engine: RulesEngine, skirmish: GameState) -> None:
    events, state = engine.run(skirmish, EndCombatAction(), NO_DICE)

    assert events == [CombatEnded(winner=None)]
    assert state.combat is None


def test_downed_character_does_not_end_the_fight_while_allies_stand(
    engine: RulesEngine, skirmish: GameState
) -> None:
    events, state = engine.run(skirmish, DamageAction(target_id="rogue", damage="50"), NO_DICE)

    assert types_of(events) == ["DamageDealt", "ConditionApplied"]
    assert state.combat is not None


# --- Список допустимых действий ---


def test_available_actions_on_own_turn(engine: RulesEngine, ambush: GameState) -> None:
    assert engine.available_actions(ambush, "fighter") == [
        AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1"),
        AttackAction(attacker_id="fighter", target_id="goblin_2", attack="attack_1"),
        AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_2"),
        AttackAction(attacker_id="fighter", target_id="goblin_2", attack="attack_2"),
        FleeAction(actor_id="fighter"),
        EndTurnAction(actor_id="fighter"),
    ]


def test_every_available_action_is_accepted_by_the_engine(
    engine: RulesEngine, ambush: GameState
) -> None:
    for action in engine.available_actions(ambush, "fighter"):
        engine.resolve(ambush, action, ScriptedRng([10, 4]))


def test_no_actions_outside_own_turn_or_combat(
    engine: RulesEngine, ambush: GameState, fighter: Actor, goblin: Actor
) -> None:
    assert engine.available_actions(ambush, "goblin_1") == []
    assert engine.available_actions(state_of(fighter, goblin), "fighter") == []


def test_spent_action_leaves_only_the_end_of_turn(engine: RulesEngine, ambush: GameState) -> None:
    attack = AttackAction(attacker_id="fighter", target_id="goblin_1", attack="attack_1")
    _, state = engine.run(ambush, attack, ScriptedRng([5]))

    assert engine.available_actions(state, "fighter") == [EndTurnAction(actor_id="fighter")]


def test_unconscious_enemies_can_still_be_attacked(
    engine: RulesEngine, skirmish: GameState
) -> None:
    _, state = engine.run(skirmish, DamageAction(target_id="rogue", damage="50"), NO_DICE)

    targets = [
        action.target_id
        for action in engine.available_actions(state, "goblin")
        if isinstance(action, AttackAction)
    ]

    assert sorted(set(targets)) == ["fighter", "rogue"]


# --- Сохранение ---


def test_state_in_combat_survives_json_round_trip(skirmish: GameState) -> None:
    assert GameState.model_validate_json(skirmish.model_dump_json()) == skirmish
