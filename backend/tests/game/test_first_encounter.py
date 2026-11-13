"""Сквозной сценарий подэтапа 1.3: персонажи из content/ проходят через движок правил.

Плутовка крадётся мимо гоблина, воин бьёт его мечом. На выходе — события и новое
состояние, а при том же seed результат повторяется.
"""

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_dnd.characters.schemas import Character
from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.rules.actions import Action, AttackAction, CheckAction
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.events import (
    ActorDied,
    AttackResolved,
    CheckResolved,
    DamageDealt,
    DiceRolled,
    Event,
)
from ai_dnd.rules.rng import ScriptedRng, SeededRng
from ai_dnd.rules.ruleset import Difficulty, Outcome
from ai_dnd.rules.state import Actor, AttackProfile, GameState, Resource
from ai_dnd.sheets.compiled import CompiledTemplate

EVENTS = TypeAdapter(list[Event])

STEALTH = CheckAction(actor_id="rogue", check="stealth", difficulty=Difficulty.MEDIUM)
SWORD = AttackAction(attacker_id="fighter", target_id="goblin", attack="attack_1")


@pytest.fixture
def state(fighter: Actor, rogue: Actor, goblin: Actor) -> GameState:
    return GameState(actors={actor.id: actor for actor in (fighter, rogue, goblin)})


def play(
    engine: RulesEngine, state: GameState, actions: list[Action], seed: int
) -> tuple[list[Event], GameState]:
    """Выполнить действия по порядку с одним генератором случайных чисел."""
    rng = SeededRng(seed)
    log: list[Event] = []
    for action in actions:
        events, state = engine.run(state, action, rng)
        log.extend(events)
    return log, state


# --- Персонаж из карточки становится участником игры ---


def test_character_sheet_becomes_an_actor(fighter: Actor, rogue: Actor, cleric: Actor) -> None:
    assert (fighter.id, fighter.name, fighter.kind) == ("fighter", "Бранд", "character")
    assert (fighter.ac, fighter.hp) == (19, Resource(current=28, max=28))
    assert fighter.checks["athletics"] == 5
    assert fighter.saves == {"str": 5, "dex": 1, "con": 4, "int": 0, "wis": 1, "cha": -1}
    assert fighter.attacks == {
        "attack_1": AttackProfile(
            name="Длинный меч", bonus=5, damage="1d8+3", damage_type="slashing"
        ),
        "attack_2": AttackProfile(
            name="Лёгкий арбалет", bonus=3, damage="1d8+1", damage_type="piercing"
        ),
    }

    assert rogue.checks["stealth"] == 7
    assert rogue.resources["hit_dice"] == Resource(current=3, max=3)

    assert cleric.resources["spell_slots_1"] == Resource(current=4, max=4)
    assert cleric.resources["spell_slots_2"] == Resource(current=2, max=2)
    assert [attack.damage_type for attack in cleric.attacks.values()] == ["bludgeoning"]


def test_hit_points_always_live_in_the_hp_resource(
    content_dir: Path, srd5_template: CompiledTemplate
) -> None:
    character = load_character(content_dir / "characters" / "en" / "rogue.json")
    sheet = build_sheet(character, srd5_template)

    actor = actor_from_sheet("mira", "Mira", sheet)

    assert sheet.hp_resource == "hp"
    assert set(actor.resources) == set(sheet.resources)
    assert actor.hp == Resource(current=21, max=21)


def test_unreadable_damage_in_the_sheet_is_an_error(srd5_template: CompiledTemplate) -> None:
    values = {"attacks": [{"name": "Кинжал", "bonus": 4, "damage": "1d4+"}]}
    sheet = build_sheet(Character(template="srd5-basic@1", values=values), srd5_template)

    with pytest.raises(ValidationError):
        actor_from_sheet("x", "Икс", sheet)


def test_attack_without_damage_and_type_is_allowed(srd5_template: CompiledTemplate) -> None:
    values = {"attacks": [{"name": "Захват", "bonus": 5}]}
    sheet = build_sheet(Character(template="srd5-basic@1", values=values), srd5_template)

    actor = actor_from_sheet("x", "Икс", sheet)

    assert actor.attacks == {"attack_1": AttackProfile(name="Захват", bonus=5)}


# --- Условие готовности подэтапа ---


def test_stealth_check_and_attack_on_the_goblin(engine: RulesEngine, state: GameState) -> None:
    # Скрытность: на кости 9, с бонусом +7 выходит 16 против 15. Атака: 14 + 5 = 19
    # против КД 15, урон 6 + 3 = 9 при семи хитах у гоблина.
    rng = ScriptedRng([9, 14, 6])

    stealth_events, after_stealth = engine.run(state, STEALTH, rng)
    attack_events, after_attack = engine.run(after_stealth, SWORD, rng)

    assert [event.type for event in stealth_events] == ["DiceRolled", "CheckResolved"]
    check = stealth_events[1]
    assert isinstance(check, CheckResolved)
    assert (check.total, check.target, check.outcome) == (16, 15, Outcome.SUCCESS)
    # Проверка состояние не меняет.
    assert after_stealth == state

    assert [event.type for event in attack_events] == [
        "DiceRolled",
        "AttackResolved",
        "DiceRolled",
        "DamageDealt",
        "ActorDied",
    ]
    attack, damage, death = attack_events[1], attack_events[3], attack_events[4]
    assert isinstance(attack, AttackResolved)
    assert isinstance(damage, DamageDealt)
    assert (attack.total, attack.target_ac, attack.outcome) == (19, 15, Outcome.SUCCESS)
    assert (damage.amount, damage.damage_type, damage.hp_after) == (9, "slashing", 0)
    assert death == ActorDied(actor_id="goblin")

    goblin = after_attack.actor("goblin")
    assert (goblin.dead, goblin.hp) == (True, Resource(current=0, max=7))
    assert after_attack.actor("fighter") == state.actor("fighter")
    assert after_attack.actor("rogue") == state.actor("rogue")
    assert rng.remaining == 0


@pytest.mark.parametrize("seed", [1, 7, 2026])
def test_same_seed_gives_the_same_events_and_state(
    engine: RulesEngine, state: GameState, seed: int
) -> None:
    actions: list[Action] = [STEALTH, SWORD]

    first_log, first_state = play(engine, state, actions, seed)
    second_log, second_state = play(engine, state, actions, seed)

    assert first_log == second_log
    assert first_state == second_state
    assert EVENTS.dump_json(first_log) == EVENTS.dump_json(second_log)
    assert first_state.model_dump_json() == second_state.model_dump_json()


def test_different_seeds_give_different_rolls(engine: RulesEngine, state: GameState) -> None:
    totals = set()
    for seed in range(30):
        log, _ = play(engine, state, [STEALTH], seed)
        rolled = log[0]
        assert isinstance(rolled, DiceRolled)
        assert 8 <= rolled.roll.total <= 27
        totals.add(rolled.roll.total)

    assert len(totals) > 5


def test_journal_can_be_stored_and_replayed(engine: RulesEngine, state: GameState) -> None:
    # Гоблин стреляет в воина несколько раз: такие действия возможны при любых бросках.
    shot = AttackAction(attacker_id="goblin", target_id="fighter", attack="shortbow")
    log, final = play(engine, state, [STEALTH, shot, shot, shot, shot], seed=3)
    assert len(log) >= 10

    # Журнал — это JSON: его можно сохранить, прочитать и проиграть заново.
    restored = EVENTS.validate_json(EVENTS.dump_json(log))

    assert restored == log
    assert apply_all(state, restored) == final
    assert GameState.model_validate_json(final.model_dump_json()) == final
