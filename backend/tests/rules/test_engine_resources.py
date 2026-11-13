"""Ресурсы, состояния с длительностью, время и отдых по набору правил 5e."""

import pytest

from ai_dnd.rules.actions import (
    ApplyConditionAction,
    PassTimeAction,
    RemoveConditionAction,
    ResourceAction,
    RestAction,
)
from ai_dnd.rules.apply import apply, apply_all
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError, RulesError
from ai_dnd.rules.events import (
    ConditionApplied,
    ConditionRemoved,
    Event,
    Healed,
    ResourceChanged,
    Rested,
    TimePassed,
)
from ai_dnd.rules.rng import ScriptedRng
from ai_dnd.rules.state import ActiveCondition, Actor, GameState, Resource

NO_DICE = ScriptedRng([])


def state_of(*actors: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in actors})


def types_of(events: list[Event]) -> list[str]:
    return [event.type for event in events]


# --- Ресурсы ---


def test_spending_a_spell_slot(engine: RulesEngine, cleric: Actor) -> None:
    action = ResourceAction(actor_id="cleric", resource="spell_slots_1", delta=-1)

    events, after = engine.run(state_of(cleric), action, NO_DICE)

    assert events == [
        ResourceChanged(actor_id="cleric", resource="spell_slots_1", delta=-1, current=3)
    ]
    assert after.actor("cleric").resources["spell_slots_1"] == Resource(current=3, max=4)
    assert after.actor("cleric").resources["spell_slots_2"] == Resource(current=2, max=2)


def test_cannot_spend_more_than_there_is(engine: RulesEngine, cleric: Actor) -> None:
    action = ResourceAction(actor_id="cleric", resource="spell_slots_2", delta=-3)

    with pytest.raises(ActionError) as error:
        engine.resolve(state_of(cleric), action, NO_DICE)

    assert error.value.code == "not_enough_resource"
    assert error.value.params == {
        "actor_id": "cleric",
        "resource": "spell_slots_2",
        "current": 2,
        "needed": 3,
    }


def test_restoring_stops_at_the_maximum(engine: RulesEngine, cleric: Actor) -> None:
    spent = cleric.with_resource("spell_slots_1", 2)

    events, after = engine.run(
        state_of(spent),
        ResourceAction(actor_id="cleric", resource="spell_slots_1", delta=5),
        NO_DICE,
    )

    assert events == [
        ResourceChanged(actor_id="cleric", resource="spell_slots_1", delta=2, current=4)
    ]
    assert after.actor("cleric").resources["spell_slots_1"] == Resource(current=4, max=4)


def test_restoring_a_full_resource_does_nothing(engine: RulesEngine, cleric: Actor) -> None:
    action = ResourceAction(actor_id="cleric", resource="spell_slots_1", delta=1)

    assert engine.resolve(state_of(cleric), action, NO_DICE) == []


@pytest.mark.parametrize(
    ("resource", "code"),
    [
        ("mana", "unknown_resource"),
        # Хиты меняются только уроном и лечением: иначе обошли бы сопротивления и «0 хитов».
        ("hp", "hit_points_need_damage_or_healing"),
    ],
)
def test_impossible_resource_changes_are_refused(
    engine: RulesEngine, cleric: Actor, resource: str, code: str
) -> None:
    with pytest.raises(ActionError) as error:
        engine.resolve(
            state_of(cleric),
            ResourceAction(actor_id="cleric", resource=resource, delta=-1),
            NO_DICE,
        )

    assert error.value.code == code


# --- Состояния ---


def test_condition_is_applied_and_removed(engine: RulesEngine, rogue: Actor) -> None:
    state = state_of(rogue)

    events, state = engine.run(
        state, ApplyConditionAction(actor_id="rogue", condition="poisoned"), NO_DICE
    )
    assert events == [ConditionApplied(actor_id="rogue", condition="poisoned", rounds=None)]
    assert state.actor("rogue").conditions == (ActiveCondition(id="poisoned"),)

    events, state = engine.run(
        state, RemoveConditionAction(actor_id="rogue", condition="poisoned"), NO_DICE
    )
    assert events == [ConditionRemoved(actor_id="rogue", condition="poisoned", reason="removed")]
    assert state.actor("rogue").conditions == ()


def test_condition_with_duration_expires(engine: RulesEngine, rogue: Actor) -> None:
    state = state_of(rogue)
    _, state = engine.run(
        state, ApplyConditionAction(actor_id="rogue", condition="frightened", rounds=2), NO_DICE
    )
    _, state = engine.run(state, ApplyConditionAction(actor_id="rogue", condition="prone"), NO_DICE)

    events, state = engine.run(state, PassTimeAction(), NO_DICE)
    assert events == [TimePassed(rounds=1)]
    assert state.actor("rogue").conditions == (
        ActiveCondition(id="frightened", rounds=1),
        ActiveCondition(id="prone"),
    )

    events, state = engine.run(state, PassTimeAction(), NO_DICE)
    assert events == [
        TimePassed(rounds=1),
        ConditionRemoved(actor_id="rogue", condition="frightened", reason="expired"),
    ]
    # Состояние без срока остаётся, пока его не снимут.
    assert state.actor("rogue").conditions == (ActiveCondition(id="prone"),)


def test_several_rounds_can_pass_at_once(engine: RulesEngine, rogue: Actor, goblin: Actor) -> None:
    state = state_of(
        rogue.with_conditions((ActiveCondition(id="blinded", rounds=3),)),
        goblin.with_conditions(
            (ActiveCondition(id="restrained", rounds=10), ActiveCondition(id="poisoned", rounds=5))
        ),
    )

    events, state = engine.run(state, PassTimeAction(rounds=5), NO_DICE)

    assert events == [
        TimePassed(rounds=5),
        ConditionRemoved(actor_id="rogue", condition="blinded", reason="expired"),
        ConditionRemoved(actor_id="goblin", condition="poisoned", reason="expired"),
    ]
    assert state.actor("rogue").conditions == ()
    assert state.actor("goblin").conditions == (ActiveCondition(id="restrained", rounds=5),)


@pytest.mark.parametrize(
    ("first", "second", "result"),
    [
        (2, 5, 5),
        (5, 2, 5),
        (3, None, None),
        (None, 3, None),
        (None, None, None),
    ],
)
def test_repeated_condition_keeps_the_longer_duration(
    engine: RulesEngine, rogue: Actor, first: int | None, second: int | None, result: int | None
) -> None:
    state = state_of(rogue)
    for rounds in (first, second):
        _, state = engine.run(
            state,
            ApplyConditionAction(actor_id="rogue", condition="poisoned", rounds=rounds),
            NO_DICE,
        )

    assert state.actor("rogue").conditions == (ActiveCondition(id="poisoned", rounds=result),)


def test_impossible_condition_changes_are_refused(engine: RulesEngine, rogue: Actor) -> None:
    state = state_of(rogue)

    with pytest.raises(ActionError) as error:
        engine.resolve(state, ApplyConditionAction(actor_id="rogue", condition="cursed"), NO_DICE)
    assert error.value.code == "unknown_condition"

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state, RemoveConditionAction(actor_id="rogue", condition="poisoned"), NO_DICE
        )
    assert error.value.code == "condition_not_active"


# --- Отдых ---


def tired(cleric: Actor) -> Actor:
    """Жрец после тяжёлого дня: ранен, ячейки и кости хитов потрачены."""
    for resource, current in (
        ("hp", 9),
        ("spell_slots_1", 0),
        ("spell_slots_2", 1),
        ("hit_dice", 0),
    ):
        cleric = cleric.with_resource(resource, current)
    return cleric


def test_long_rest_restores_resources(engine: RulesEngine, cleric: Actor) -> None:
    events, after = engine.run(state_of(tired(cleric)), RestAction(rest="long"), NO_DICE)

    assert events[0] == Rested(rest="long", actor_ids=("cleric",))
    assert Healed(target_id="cleric", source_id=None, amount=18, rolled=18, hp_after=27) in events
    rested = after.actor("cleric")
    assert rested.hp == Resource(current=27, max=27)
    assert rested.resources["spell_slots_1"] == Resource(current=4, max=4)
    assert rested.resources["spell_slots_2"] == Resource(current=2, max=2)
    # Кости хитов возвращаются наполовину: из трёх — одна.
    assert rested.resources["hit_dice"] == Resource(current=1, max=3)
    # Ячейки, которых у персонажа нет, событий не дают.
    changed = [event.resource for event in events if isinstance(event, ResourceChanged)]
    assert sorted(changed) == ["hit_dice", "spell_slots_1", "spell_slots_2"]


def test_short_rest_does_not_restore_long_rest_resources(
    engine: RulesEngine, cleric: Actor
) -> None:
    before = tired(cleric)

    events, after = engine.run(state_of(before), RestAction(rest="short"), NO_DICE)

    assert events == [Rested(rest="short", actor_ids=("cleric",))]
    assert after.actor("cleric") == before


def test_rested_actor_has_nothing_to_restore(engine: RulesEngine, cleric: Actor) -> None:
    events = engine.resolve(state_of(cleric), RestAction(rest="long"), NO_DICE)

    assert events == [Rested(rest="long", actor_ids=("cleric",))]


def test_long_rest_wakes_up_an_unconscious_character(engine: RulesEngine, rogue: Actor) -> None:
    downed = rogue.with_resource("hp", 0).with_conditions((ActiveCondition(id="unconscious"),))

    events, after = engine.run(state_of(downed), RestAction(rest="long"), NO_DICE)

    assert ConditionRemoved(actor_id="rogue", condition="unconscious", reason="healed") in events
    assert after.actor("rogue").hp == Resource(current=21, max=21)
    assert after.actor("rogue").conditions == ()


def test_rest_is_for_everyone_alive_unless_told_otherwise(
    engine: RulesEngine, cleric: Actor, rogue: Actor, goblin: Actor
) -> None:
    dead = goblin.model_copy(update={"dead": True})
    state = state_of(tired(cleric), rogue.with_resource("hp", 5), dead)

    events, after = engine.run(state, RestAction(rest="long"), NO_DICE)
    assert events[0] == Rested(rest="long", actor_ids=("cleric", "rogue"))
    assert after.actor("rogue").hp == Resource(current=21, max=21)

    events, after = engine.run(state, RestAction(rest="long", actor_ids=("rogue",)), NO_DICE)
    assert events[0] == Rested(rest="long", actor_ids=("rogue",))
    assert after.actor("cleric") == tired(cleric)

    with pytest.raises(ActionError) as error:
        engine.resolve(state, RestAction(rest="long", actor_ids=("goblin",)), NO_DICE)
    assert error.value.code == "actor_dead"


# --- Применение событий ---


def test_apply_keeps_values_within_bounds(cleric: Actor) -> None:
    state = state_of(cleric)

    overspent = apply(
        state, ResourceChanged(actor_id="cleric", resource="spell_slots_1", delta=-10, current=0)
    )
    overhealed = apply(
        state, Healed(target_id="cleric", source_id=None, amount=100, rolled=100, hp_after=27)
    )

    assert overspent.actor("cleric").resources["spell_slots_1"] == Resource(current=0, max=4)
    assert overhealed.actor("cleric").hp == Resource(current=27, max=27)


def test_apply_ignores_removal_of_a_missing_condition(rogue: Actor) -> None:
    state = state_of(rogue)

    after = apply(state, ConditionRemoved(actor_id="rogue", condition="prone", reason="expired"))

    assert after == state


def test_apply_refuses_events_about_unknown_things(rogue: Actor) -> None:
    state = state_of(rogue)

    with pytest.raises(RulesError) as error:
        apply(state, ConditionApplied(actor_id="ghost", condition="prone", rounds=None))
    assert error.value.code == "unknown_actor"

    with pytest.raises(RulesError) as error:
        apply(state, ResourceChanged(actor_id="rogue", resource="mana", delta=-1, current=0))
    assert error.value.code == "unknown_resource"


def test_time_passing_alone_drops_expired_conditions(rogue: Actor) -> None:
    state = state_of(rogue.with_conditions((ActiveCondition(id="prone", rounds=1),)))

    assert apply_all(state, [TimePassed(rounds=1)]).actor("rogue").conditions == ()
