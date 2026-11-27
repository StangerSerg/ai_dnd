"""Юнит-тесты числового боезапаса атак (ammo_resource), фикс «Червей».

Без БД/сети/LLM: бой идёт через GameMaster на копии сценария «five-guardians»
в памяти, броски заданы ScriptedRng, провайдер — FakeProvider. Воину добавлены
ресурс «missiles» и две атаки: «rocket» с ammo_resource и «laser» без расхода.
Проверяются расход за попытку (попадание и промах), отказ out_of_ammo при нуле,
скрытие атаки в affordable_rules/combat_options, идентичность replay и отказ
движка использовать hp как боезапас.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.providers.fake import FakeProvider
from ai_dnd.ai.recorder import MemoryRecorder
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import ItemSpent, JournalEvent
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import (
    SessionSetup,
    SessionState,
    affordable_rules,
    apply_events,
)
from ai_dnd.game.tools import DmTools, ToolOutcome, item_mechanics
from ai_dnd.rules.actions import AttackAction, EndTurnAction
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import AttackResolved, ResourceChanged
from ai_dnd.rules.rng import Rng, ScriptedRng
from ai_dnd.rules.state import Actor, AttackProfile, Resource
from ai_dnd.scenarios.schemas import Encounter, EncounterGroup, Statblock, StatblockAttack

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
START_SCENE = "tavern"
FIGHT = "v2_fight"
DUMMY = "v2_dummy"

# Манекен: высокий КД (промах без урона), много хитов, инициатива заведомо последняя.
DUMMY_BLOCK = Statblock(
    name="Манекен",
    ac=20,
    hp=50,
    initiative=-10,
    checks={},
    attacks={"tap": StatblockAttack(name="Тычок", bonus=0, damage="1")},
)
DUMMY_FIGHT = Encounter(id=FIGHT, title="Пробный бой", creatures=(EncounterGroup(statblock=DUMMY),))

ROCKET = AttackProfile(name="Ракетная установка", bonus=4, damage="1", ammo_resource="missiles")
LASER = AttackProfile(name="Лазерная установка", bonus=4, damage="1")


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    base = load_game_content(content_dir, SCENARIO, "ru")
    scenario = base.scenario
    scenes = tuple(
        scene.model_copy(update={"encounters": (*scene.encounters, FIGHT)})
        if scene.id == START_SCENE
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(
        update={
            "statblocks": {**scenario.statblocks, DUMMY: DUMMY_BLOCK},
            "encounters": (*scenario.encounters, DUMMY_FIGHT),
            "scenes": scenes,
        }
    )
    return replace(base, scenario=patched)


def _with_ship_attacks(state: SessionState, missiles: int = 2) -> SessionState:
    """Воин с числовым боезапасом: ресурс missiles и атака, которая его тратит."""
    fighter = state.rules.actor("fighter")
    armed = fighter.model_copy(
        update={
            "resources": {**fighter.resources, "missiles": Resource(current=missiles, max=2)},
            "attacks": {"rocket": ROCKET, "laser": LASER},
        }
    )
    return state.model_copy(update={"rules": state.rules.with_actor(armed)})


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    return setup, _with_ship_attacks(new_session_state(content, actors))


def make_tools(content: GameContent, setup: SessionSetup, rng: Rng) -> DmTools:
    engine = RulesEngine(content.ruleset, item_mechanics(content.scenario))
    return DmTools(content, setup, engine, rng)


def call(tools: DmTools, state: SessionState, name: str, **arguments: Any) -> ToolOutcome:
    return tools.execute(state, ToolCall(id="test", name=name, arguments=arguments))


def make_master(
    content: GameContent,
    setup: SessionSetup,
    state: SessionState,
    rng: Rng,
    llm_config: LLMConfig,
) -> tuple[GameMaster, MemoryJournal]:
    gateway = LLMGateway(
        llm_config, dict.fromkeys(llm_config.providers, FakeProvider()), MemoryRecorder()
    )
    journal = MemoryJournal()
    return GameMaster(content, setup, state, gateway, journal, rng), journal


async def tool(master: GameMaster, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = call(master._tools, master.state, name, **arguments)
    assert not outcome.failed, (name, arguments, outcome.result)
    await master._commit(outcome.events)
    return outcome.result


async def start_fight(master: GameMaster) -> None:
    await tool(master, "start_combat", encounter_id=FIGHT)
    assert master.current_combatant == "fighter"


async def back_to_fighter(master: GameMaster) -> None:
    """Докрутить очередь до воина ходами EndTurn — без бросков и тактики."""
    for _ in range(6):
        current = master.current_combatant
        if not master.in_combat or current == "fighter":
            return
        assert current is not None
        await master.act(EndTurnAction(actor_id=current))
    raise AssertionError("очередь не дошла до воина")


async def attack_and_cycle(master: GameMaster, attack_id: str) -> list[JournalEvent]:
    events = await master.act(
        AttackAction(attacker_id="fighter", target_id=DUMMY, attack=attack_id)
    )
    # Атака тратит действие, но не завершает ход: конец хода шлём отдельно.
    if master.in_combat:
        await master.act(EndTurnAction(actor_id="fighter"))
    await back_to_fighter(master)
    return events


def _missiles(state: SessionState) -> Resource:
    resource = state.rules.actor("fighter").resources.get("missiles")
    assert resource is not None
    return resource


# --- ammo_resource: ресурс тратится за попытку, включая промах ---


async def test_resource_ammo_spent_on_hit_and_miss(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    # Инициатива: воин 20, жрец 10, манекен 1; броски атак — промах 1, затем попадание 20.
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 1, 20]), llm_config)
    await start_fight(master)

    first = await attack_and_cycle(master, "rocket")

    assert isinstance(first[0], ResourceChanged)
    assert first[0].actor_id == "fighter"
    assert first[0].resource == "missiles"
    assert first[0].delta == -1
    assert first[0].current == 1
    resolved = next(event for event in first if isinstance(event, AttackResolved))
    assert not resolved.outcome.succeeded
    assert first.index(resolved) > 0  # расход записан до событий движка
    assert _missiles(master.state).current == 1

    second = await attack_and_cycle(master, "rocket")

    resolved = next(event for event in second if isinstance(event, AttackResolved))
    assert resolved.outcome.succeeded
    assert isinstance(second[0], ResourceChanged)
    assert _missiles(master.state).current == 0
    assert apply_events(base, journal.events) == master.state


# --- Ноль боезапаса: атака скрыта и отвергается, журнал не меняется ---


async def test_empty_resource_hides_attack_and_rejects(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    empty = _with_ship_attacks(base, missiles=0)
    master, journal = make_master(content, setup, empty, ScriptedRng([20, 10, 1]), llm_config)
    await start_fight(master)

    options = master.combat_options("fighter")
    assert options
    assert not [
        option
        for option in options
        if isinstance(option, AttackAction) and option.attack == "rocket"
    ]
    assert [
        option
        for option in options
        if isinstance(option, AttackAction) and option.attack == "laser"
    ]
    assert "rocket" not in affordable_rules(master.state, "fighter").actor("fighter").attacks

    before = len(journal.events)
    with pytest.raises(ActionError) as error:
        await master.act(AttackAction(attacker_id="fighter", target_id=DUMMY, attack="rocket"))
    assert error.value.code == "out_of_ammo"
    assert error.value.params["item_id"] == "missiles"
    assert len(journal.events) == before
    assert apply_events(empty, journal.events) == master.state


# --- Атака без ammo_resource ничего не тратит ---


async def test_attack_without_ammo_resource_spends_nothing(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20]), llm_config)
    await start_fight(master)

    events = await attack_and_cycle(master, "laser")

    resolved = next(event for event in events if isinstance(event, AttackResolved))
    assert resolved.outcome.succeeded
    assert not [event for event in events if isinstance(event, ResourceChanged | ItemSpent)]
    assert _missiles(master.state).current == 2
    assert apply_events(base, journal.events) == master.state


# --- hp как боезапас движок отвергает: урон и лечение идут своими действиями ---


async def test_hp_as_ammo_resource_rejected_by_engine(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    fighter = base.rules.actor("fighter")
    bad = fighter.model_copy(
        update={
            "attacks": {
                **fighter.attacks,
                "bad": AttackProfile(name="Плохая", bonus=4, damage="1", ammo_resource="hp"),
            }
        }
    )
    patched = base.model_copy(update={"rules": base.rules.with_actor(bad)})
    master, journal = make_master(content, setup, patched, ScriptedRng([20, 10, 1]), llm_config)
    await start_fight(master)

    before = len(journal.events)
    with pytest.raises(ActionError) as error:
        await master.act(AttackAction(attacker_id="fighter", target_id=DUMMY, attack="bad"))
    assert error.value.code == "hit_points_need_damage_or_healing"
    assert len(journal.events) == before
    assert apply_events(patched, journal.events) == master.state


# --- Ресурса у актёра нет: атака с ammo_resource скрыта, как нет стафа ---


def test_missing_resource_hides_attack(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, base = session_pair
    fighter = base.rules.actor("fighter")
    stripped: Actor = fighter.model_copy(
        update={
            "resources": {
                key: value for key, value in fighter.resources.items() if key != "missiles"
            }
        }
    )
    state = base.model_copy(update={"rules": base.rules.with_actor(stripped)})

    attacks = affordable_rules(state, "fighter").actor("fighter").attacks
    assert "rocket" not in attacks
    assert "laser" in attacks
