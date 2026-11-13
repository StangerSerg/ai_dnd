"""Атаки, урон, лечение и правило «0 хитов» по набору правил 5e."""

import pytest

from ai_dnd.rules.actions import AttackAction, DamageAction, HealAction
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import (
    ActorDied,
    AttackResolved,
    ConditionApplied,
    ConditionRemoved,
    DamageDealt,
    DiceRolled,
    Event,
    Healed,
)
from ai_dnd.rules.rng import ScriptedRng
from ai_dnd.rules.ruleset import Outcome
from ai_dnd.rules.state import ActiveCondition, Actor, AttackProfile, GameState, Resource
from ai_dnd.rules.visibility import DM


def state_of(*actors: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in actors})


def with_condition(actor: Actor, condition: str) -> Actor:
    return actor.with_conditions((*actor.conditions, ActiveCondition(id=condition)))


def with_hp(actor: Actor, current: int) -> Actor:
    return actor.with_resource("hp", current)


def types_of(events: list[Event]) -> list[str]:
    return [event.type for event in events]


def sword_attack(mode: RollMode = RollMode.NORMAL) -> AttackAction:
    """Воин бьёт гоблина длинным мечом: +5 к атаке, урон 1d8+3 рубящий."""
    return AttackAction(attacker_id="fighter", target_id="goblin", attack="attack_1", mode=mode)


# --- Атака ---


def test_hit_deals_damage_and_kills_the_goblin(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    state = state_of(fighter, goblin)

    # На кости атаки 12, на кости урона 5.
    events, after = engine.run(state, sword_attack(), ScriptedRng([12, 5]))

    attack_roll, attack, damage_roll, damage, death = events
    assert isinstance(attack_roll, DiceRolled)
    assert (attack_roll.actor_id, attack_roll.reason) == ("fighter", "attack")
    assert str(attack_roll.roll) == "1d20 + 5 → [12] + 5 = 17"
    assert attack == AttackResolved(
        attacker_id="fighter",
        target_id="goblin",
        attack="attack_1",
        attack_name="Длинный меч",
        outcome=Outcome.SUCCESS,
        total=17,
        natural=12,
        target_ac=15,
        mode=RollMode.NORMAL,
    )
    assert isinstance(damage_roll, DiceRolled)
    assert (damage_roll.actor_id, damage_roll.reason) == ("fighter", "damage")
    assert str(damage_roll.roll) == "1d8 + 3 → [5] + 3 = 8"
    assert damage == DamageDealt(
        target_id="goblin",
        source_id="fighter",
        amount=8,
        rolled=8,
        damage_type="slashing",
        hp_after=0,
    )
    assert death == ActorDied(actor_id="goblin")

    assert after.actor("goblin").dead is True
    assert after.actor("goblin").hp == Resource(current=0, max=7)
    assert after.actor("fighter") == fighter
    # Исходное состояние не тронуто.
    assert state.actor("goblin").hp == Resource(current=7, max=7)


def test_wounded_goblin_survives_a_weak_hit(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    events, after = engine.run(state_of(fighter, goblin), sword_attack(), ScriptedRng([10, 1]))

    assert types_of(events) == ["DiceRolled", "AttackResolved", "DiceRolled", "DamageDealt"]
    assert after.actor("goblin").hp == Resource(current=3, max=7)
    assert after.actor("goblin").dead is False


def test_miss_deals_no_damage(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    rng = ScriptedRng([9, 8])

    events, after = engine.run(state_of(fighter, goblin), sword_attack(), rng)

    assert types_of(events) == ["DiceRolled", "AttackResolved"]
    attack = events[1]
    assert isinstance(attack, AttackResolved)
    assert (attack.total, attack.target_ac, attack.outcome) == (14, 15, Outcome.FAILURE)
    assert after.actor("goblin") == goblin
    # Кость урона не брошена.
    assert rng.remaining == 1


def test_natural_twenty_is_a_critical_hit_with_doubled_dice(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    tough = goblin.model_copy(update={"ac": 30, "resources": {"hp": Resource(current=50, max=50)}})

    events, after = engine.run(state_of(fighter, tough), sword_attack(), ScriptedRng([20, 4, 6]))

    attack, damage_roll = events[1], events[2]
    assert isinstance(attack, AttackResolved)
    assert isinstance(damage_roll, DiceRolled)
    # 25 против КД 30 — промах бы, но натуральная 20 попадает всегда.
    assert (attack.total, attack.outcome) == (25, Outcome.CRITICAL_SUCCESS)
    # Удваиваются кости, а не модификатор.
    assert str(damage_roll.roll) == "2d8 + 3 → [4, 6] + 3 = 13"
    assert after.actor("goblin").hp == Resource(current=37, max=50)


def test_natural_one_always_misses(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    easy_target = goblin.model_copy(update={"ac": 5})

    events = engine.resolve(state_of(fighter, easy_target), sword_attack(), ScriptedRng([1]))

    attack = events[1]
    assert isinstance(attack, AttackResolved)
    assert (attack.total, attack.target_ac, attack.outcome) == (6, 5, Outcome.CRITICAL_FAILURE)
    assert types_of(events) == ["DiceRolled", "AttackResolved"]


def test_creature_attacks_a_character(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    action = AttackAction(attacker_id="goblin", target_id="fighter", attack="scimitar")

    # Скимитар +4 против КД 19: нужно 15 на кости. Урон 1d6+2.
    events, after = engine.run(state_of(fighter, goblin), action, ScriptedRng([15, 4]))

    assert types_of(events) == ["DiceRolled", "AttackResolved", "DiceRolled", "DamageDealt"]
    assert after.actor("fighter").hp == Resource(current=22, max=28)


def test_attack_mode_from_circumstances(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    events = engine.resolve(
        state_of(fighter, goblin), sword_attack(RollMode.ADVANTAGE), ScriptedRng([3, 14, 2])
    )

    attack_roll, attack = events[0], events[1]
    assert isinstance(attack_roll, DiceRolled)
    assert isinstance(attack, AttackResolved)
    assert attack_roll.roll.notation == "2d20kh1 + 5"
    assert (attack.natural, attack.mode, attack.outcome) == (
        14,
        RollMode.ADVANTAGE,
        Outcome.SUCCESS,
    )


@pytest.mark.parametrize(
    ("attacker_condition", "target_condition", "mode"),
    [
        ("blinded", None, RollMode.DISADVANTAGE),
        ("poisoned", None, RollMode.DISADVANTAGE),
        ("invisible", None, RollMode.ADVANTAGE),
        (None, "restrained", RollMode.ADVANTAGE),
        (None, "invisible", RollMode.DISADVANTAGE),
        (None, "stunned", RollMode.ADVANTAGE),
        # Преимущество и помеха гасят друг друга.
        ("blinded", "restrained", RollMode.NORMAL),
        ("invisible", "invisible", RollMode.NORMAL),
        (None, "charmed", RollMode.NORMAL),
    ],
)
def test_conditions_change_attack_rolls(
    engine: RulesEngine,
    fighter: Actor,
    goblin: Actor,
    attacker_condition: str | None,
    target_condition: str | None,
    mode: RollMode,
) -> None:
    attacker = with_condition(fighter, attacker_condition) if attacker_condition else fighter
    target = with_condition(goblin, target_condition) if target_condition else goblin

    events = engine.resolve(state_of(attacker, target), sword_attack(), ScriptedRng([2, 2, 2]))

    attack = events[1]
    assert isinstance(attack, AttackResolved)
    assert attack.mode is mode


def test_attack_without_damage_only_resolves_the_roll(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    grappler = fighter.model_copy(
        update={"attacks": {"shove": AttackProfile(name="Толчок", bonus=5)}}
    )
    action = AttackAction(attacker_id="fighter", target_id="goblin", attack="shove")

    events = engine.resolve(state_of(grappler, goblin), action, ScriptedRng([15]))

    assert types_of(events) == ["DiceRolled", "AttackResolved"]


def test_hidden_attack_stays_hidden(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    action = AttackAction(
        attacker_id="goblin", target_id="fighter", attack="shortbow", visibility=DM
    )

    events = engine.resolve(state_of(fighter, goblin), action, ScriptedRng([18, 3]))

    assert len(events) == 4
    assert {event.visibility for event in events} == {DM}


# --- Сопротивления, уязвимости, иммунитеты ---


@pytest.mark.parametrize(
    ("traits", "amount", "modifiers"),
    [
        ({}, 8, ()),
        ({"resistances": ("slashing",)}, 4, ("resistance",)),
        ({"vulnerabilities": ("slashing",)}, 16, ("vulnerability",)),
        ({"immunities": ("slashing",)}, 0, ("immunity",)),
        # Сначала сопротивление, потом уязвимость.
        (
            {"resistances": ("slashing",), "vulnerabilities": ("slashing",)},
            8,
            ("resistance", "vulnerability"),
        ),
        # Чужой тип урона не влияет.
        ({"resistances": ("fire",), "immunities": ("poison",)}, 8, ()),
    ],
)
def test_damage_type_changes_the_amount(
    engine: RulesEngine,
    fighter: Actor,
    goblin: Actor,
    traits: dict[str, tuple[str, ...]],
    amount: int,
    modifiers: tuple[str, ...],
) -> None:
    target = goblin.model_copy(update={**traits, "resources": {"hp": Resource(current=40, max=40)}})

    events, after = engine.run(state_of(fighter, target), sword_attack(), ScriptedRng([15, 5]))

    damage = events[-1]
    assert isinstance(damage, DamageDealt)
    assert (damage.rolled, damage.amount, damage.modifiers) == (8, amount, modifiers)
    assert after.actor("goblin").hp == Resource(current=40 - amount, max=40)


def test_resistance_rounds_down(engine: RulesEngine, goblin: Actor) -> None:
    target = goblin.model_copy(update={"resistances": ("fire",)})
    action = DamageAction(target_id="goblin", damage="5", damage_type="fire")

    events = engine.resolve(state_of(target), action, ScriptedRng([]))

    assert events == [
        DamageDealt(
            target_id="goblin",
            source_id=None,
            amount=2,
            rolled=5,
            damage_type="fire",
            modifiers=("resistance",),
            hp_after=5,
        )
    ]


# --- Урон без атаки ---


def test_trap_damage(engine: RulesEngine, rogue: Actor) -> None:
    action = DamageAction(target_id="rogue", damage="2d6", damage_type="fire")

    events, after = engine.run(state_of(rogue), action, ScriptedRng([3, 5]))

    roll, damage = events
    assert isinstance(roll, DiceRolled)
    assert (roll.actor_id, roll.reason, roll.roll.total) == (None, "damage", 8)
    assert isinstance(damage, DamageDealt)
    assert (damage.source_id, damage.amount, damage.hp_after) == (None, 8, 13)
    assert after.actor("rogue").hp == Resource(current=13, max=21)


def test_negative_damage_roll_is_zero(engine: RulesEngine, rogue: Actor) -> None:
    action = DamageAction(target_id="rogue", damage="1d4-3")

    events, after = engine.run(state_of(rogue), action, ScriptedRng([1]))

    damage = events[-1]
    assert isinstance(damage, DamageDealt)
    assert (damage.rolled, damage.amount, damage.damage_type) == (0, 0, None)
    assert after.actor("rogue") == rogue


# --- 0 хитов ---


def test_character_at_zero_hit_points_falls_unconscious(engine: RulesEngine, rogue: Actor) -> None:
    action = DamageAction(target_id="rogue", damage="40", damage_type="fire")

    events, after = engine.run(state_of(rogue), action, ScriptedRng([]))

    assert events == [
        DamageDealt(
            target_id="rogue", source_id=None, amount=40, rolled=40, damage_type="fire", hp_after=0
        ),
        ConditionApplied(actor_id="rogue", condition="unconscious", rounds=None),
    ]
    downed = after.actor("rogue")
    assert downed.hp == Resource(current=0, max=21)
    assert downed.dead is False
    assert downed.condition("unconscious") == ActiveCondition(id="unconscious")


def test_more_damage_at_zero_changes_nothing_else(engine: RulesEngine, rogue: Actor) -> None:
    downed = with_condition(with_hp(rogue, 0), "unconscious")
    action = DamageAction(target_id="rogue", damage="5")

    events, after = engine.run(state_of(downed), action, ScriptedRng([]))

    assert types_of(events) == ["DamageDealt"]
    assert after.actor("rogue") == downed


def test_unconscious_character_is_easier_to_hit_and_cannot_attack(
    engine: RulesEngine, rogue: Actor, goblin: Actor
) -> None:
    downed = with_condition(with_hp(rogue, 0), "unconscious")
    state = state_of(downed, goblin)

    events = engine.resolve(
        state,
        AttackAction(attacker_id="goblin", target_id="rogue", attack="scimitar"),
        ScriptedRng([2, 9]),
    )
    attack = events[1]
    assert isinstance(attack, AttackResolved)
    assert attack.mode is RollMode.ADVANTAGE

    with pytest.raises(ActionError) as error:
        engine.resolve(
            state,
            AttackAction(attacker_id="rogue", target_id="goblin", attack="attack_1"),
            ScriptedRng([]),
        )
    assert error.value.code == "actor_incapacitated"


# --- Лечение ---


def test_healing_restores_hit_points(engine: RulesEngine, fighter: Actor) -> None:
    wounded = with_hp(fighter, 10)
    action = HealAction(target_id="fighter", amount="1d8+3", source_id="cleric")

    events, after = engine.run(state_of(wounded), action, ScriptedRng([6]))

    roll, healed = events
    assert isinstance(roll, DiceRolled)
    assert (roll.actor_id, roll.reason) == ("cleric", "healing")
    assert healed == Healed(
        target_id="fighter", source_id="cleric", amount=9, rolled=9, hp_after=19
    )
    assert after.actor("fighter").hp == Resource(current=19, max=28)


def test_healing_stops_at_the_maximum(engine: RulesEngine, fighter: Actor) -> None:
    wounded = with_hp(fighter, 25)

    events, after = engine.run(
        state_of(wounded), HealAction(target_id="fighter", amount="10"), ScriptedRng([])
    )

    # Число без костей — событие броска не нужно.
    assert events == [Healed(target_id="fighter", source_id=None, amount=3, rolled=10, hp_after=28)]
    assert after.actor("fighter").hp == Resource(current=28, max=28)


def test_healing_wakes_up_an_unconscious_character(engine: RulesEngine, rogue: Actor) -> None:
    downed = with_condition(with_hp(rogue, 0), "unconscious")

    events, after = engine.run(
        state_of(downed), HealAction(target_id="rogue", amount="1d4"), ScriptedRng([2])
    )

    assert events[1:] == [
        Healed(target_id="rogue", source_id=None, amount=2, rolled=2, hp_after=2),
        ConditionRemoved(actor_id="rogue", condition="unconscious", reason="healed"),
    ]
    revived = after.actor("rogue")
    assert revived.hp == Resource(current=2, max=21)
    assert revived.conditions == ()


# --- Чего сделать нельзя ---


def test_impossible_attacks_are_refused(engine: RulesEngine, fighter: Actor, goblin: Actor) -> None:
    dead = goblin.model_copy(update={"dead": True})
    no_armor = goblin.model_copy(update={"ac": None})
    no_hp = goblin.model_copy(update={"resources": {}})
    cases = [
        # Урон нанести некуда — это выясняется до броска атаки, а не после.
        (state_of(fighter, no_hp), sword_attack(), "no_hit_points"),
        (
            state_of(fighter, goblin),
            sword_attack().model_copy(update={"attack": "halberd"}),
            "unknown_attack",
        ),
        (state_of(fighter), sword_attack(), "unknown_actor"),
        (state_of(fighter, dead), sword_attack(), "actor_dead"),
        (state_of(fighter, no_armor), sword_attack(), "no_armor_class"),
        (
            state_of(fighter, dead),
            AttackAction(attacker_id="goblin", target_id="fighter", attack="scimitar"),
            "actor_dead",
        ),
    ]

    for state, action, code in cases:
        rng = ScriptedRng([10, 10])
        with pytest.raises(ActionError) as error:
            engine.resolve(state, action, rng)
        assert error.value.code == code
        assert rng.remaining == 2


def test_impossible_damage_and_healing_are_refused(engine: RulesEngine, goblin: Actor) -> None:
    dead = goblin.model_copy(update={"dead": True})
    no_hp = goblin.model_copy(update={"resources": {}})
    cases: list[tuple[GameState, DamageAction | HealAction, str]] = [
        (
            state_of(goblin),
            DamageAction(target_id="goblin", damage="5", damage_type="sonic"),
            "unknown_damage_type",
        ),
        (state_of(no_hp), DamageAction(target_id="goblin", damage="5"), "no_hit_points"),
        (state_of(dead), DamageAction(target_id="goblin", damage="5"), "actor_dead"),
        (state_of(dead), HealAction(target_id="goblin", amount="5"), "actor_dead"),
        (state_of(no_hp), HealAction(target_id="goblin", amount="5"), "no_hit_points"),
    ]

    for state, action, code in cases:
        with pytest.raises(ActionError) as error:
            engine.resolve(state, action, ScriptedRng([]))
        assert error.value.code == code


def test_events_can_be_replayed_onto_the_initial_state(
    engine: RulesEngine, fighter: Actor, goblin: Actor
) -> None:
    initial = state_of(fighter, goblin)

    events, after = engine.run(initial, sword_attack(), ScriptedRng([18, 2]))

    assert apply_all(initial, events) == after
    assert after.actor("goblin").hp == Resource(current=2, max=7)
