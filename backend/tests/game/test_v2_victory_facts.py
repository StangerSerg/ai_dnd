"""Юнит-тесты авто-фактов победы (victory_facts) и гарантии лута на всех путях коммита.

Без БД/сети/LLM: бой идёт через реальный GameMaster/DmTools на копии сценария
«five-guardians» в памяти, броски заданы ScriptedRng, LLM-заглушка падает при
любом вызове. Проверяются FactNoted+лут тем же коммитом, что CombatEnded, для
пути действий (act) и пути DM-тул (_commit_operation, adjust_resource добивает
последнего врага), согласованность тул-рекорда/public_events/EventsHappened,
дедупликация фактов и лута (включая retry после потери блокировки), энкаунтер
без victory_facts и идентичность replay из журнала для обоих путей.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import JOURNAL_EVENT, FactNoted, ItemGranted, Revealed
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import (
    EventsHappened,
    GameMaster,
    TurnLockLostError,
    TurnUpdate,
    _PendingOperation,
    _PendingTurn,
)
from ai_dnd.game.session import SessionSetup, SessionState, apply_events, item_stock
from ai_dnd.game.tools import ToolOutcome
from ai_dnd.rules.actions import AttackAction
from ai_dnd.rules.events import CombatEnded
from ai_dnd.rules.rng import Rng, ScriptedRng
from ai_dnd.rules.state import HIT_POINTS, AttackProfile
from ai_dnd.scenarios.schemas import (
    Encounter,
    EncounterGroup,
    Item,
    LootEntry,
    Statblock,
    StatblockAttack,
)

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
WEAK = "v2_weakling"
FACT_FIGHT = "v2_fact_fight"
PLAIN_FIGHT = "v2_plain_fight"
FACT_ONLY_FIGHT = "v2_fact_only_fight"
FACT_TEXT = "v2_training_won"

COIN = Item(id="v2_coin", name="Монеты добычи", quantity=5)
GEM = Item(id="v2_gem", name="Самоцвет добычи")

WEAKLING = Statblock(
    name="Слабак",
    ac=1,
    hp=1,
    initiative=-10,
    checks={},
    attacks={"claw": StatblockAttack(name="Удар", bonus=0, damage="1")},
)
ENCOUNTERS = (
    Encounter(
        id=FACT_FIGHT,
        title="Бой с фактом и добычей",
        creatures=(EncounterGroup(statblock=WEAK),),
        loot=(LootEntry(item_id="v2_coin", quantity=2),),
        victory_facts=(FACT_TEXT,),
    ),
    Encounter(
        id=PLAIN_FIGHT,
        title="Бой без фактов",
        creatures=(EncounterGroup(statblock=WEAK),),
        loot=(LootEntry(item_id="v2_gem"),),
    ),
    Encounter(
        id=FACT_ONLY_FIGHT,
        title="Бой только с фактом",
        creatures=(EncounterGroup(statblock=WEAK),),
        victory_facts=(FACT_TEXT,),
    ),
)


class NoLLM:
    async def complete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline test must not call the LLM")


@pytest.fixture(scope="session")
def base_content(content_dir: Path) -> GameContent:
    return load_game_content(content_dir, SCENARIO, "ru")


@pytest.fixture(scope="session")
def content(base_content: GameContent) -> GameContent:
    scenario = base_content.scenario
    scenes = tuple(
        scene.model_copy(
            update={"encounters": (*scene.encounters, *(entry.id for entry in ENCOUNTERS))}
        )
        if scene.id == scenario.scenario.start_scene
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(
        update={
            "items": (*scenario.items, COIN, GEM),
            "statblocks": {**scenario.statblocks, WEAK: WEAKLING},
            "encounters": (*scenario.encounters, *ENCOUNTERS),
            "scenes": scenes,
        }
    )
    return replace(base_content, scenario=patched)


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    base = new_session_state(content, actors)
    # Фиксированный урон «1» не тратит бросков: скрипт rng полностью предсказуем.
    fighter = base.rules.actor("fighter")
    armed = fighter.model_copy(
        update={"attacks": {"strike": AttackProfile(name="Удар", bonus=4, damage="1")}}
    )
    return setup, base.model_copy(update={"rules": base.rules.with_actor(armed)})


def make_master(
    content: GameContent, setup: SessionSetup, state: SessionState, rng: Rng
) -> tuple[GameMaster, MemoryJournal]:
    journal = MemoryJournal()
    return GameMaster(content, setup, state, cast(LLMGateway, NoLLM()), journal, rng), journal


def call(master: GameMaster, name: str, **arguments: Any) -> ToolOutcome:
    return master._tools.execute(master.state, ToolCall(id="test", name=name, arguments=arguments))


async def start_fight(master: GameMaster, encounter_id: str) -> None:
    outcome = call(master, "start_combat", encounter_id=encounter_id)
    assert not outcome.failed, (encounter_id, outcome.result)
    await master._commit(outcome.events)


async def win_with_strike(master: GameMaster, encounter_id: str) -> list[Any]:
    """Инициатива и попадание заданы скриптом: воин убивает слабака первым ударом."""
    await start_fight(master, encounter_id)
    assert master.current_combatant == "fighter"
    events = await master.act(AttackAction(attacker_id="fighter", target_id=WEAK, attack="strike"))
    ended = next(event for event in events if isinstance(event, CombatEnded))
    assert ended.winner == "party"
    assert not master.in_combat
    return events


def trap_operation(master: GameMaster) -> _PendingOperation:
    """DM-тула adjust_resource: урон среды добивает последнего врага (hp 1)."""
    arguments = {"actor_id": WEAK, "resource": HIT_POINTS, "delta": -1, "reason": "обвал"}
    outcome = call(master, "adjust_resource", **arguments)
    assert not outcome.failed, outcome.result
    return _PendingOperation(
        refs=(0,), name="adjust_resource", arguments=arguments, outcome=outcome
    )


async def drain(
    master: GameMaster, pending: _PendingTurn, operation: _PendingOperation
) -> list[TurnUpdate]:
    return [update async for update in master._commit_operation(pending, operation)]


def granted_of(events: list[Any]) -> list[ItemGranted]:
    return [event for event in events if isinstance(event, ItemGranted)]


def noted_of(events: list[Any]) -> list[FactNoted]:
    return [event for event in events if isinstance(event, FactNoted)]


# --- Победа через act: факт и лут тем же коммитом, что CombatEnded ---


async def test_act_victory_fact_and_loot_same_commit(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    # Инициатива 20/10/1, атака 20, пер-милле бросок лута 500 (шанс 1.0).
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20, 500]))

    events = await win_with_strike(master, FACT_FIGHT)

    ended_index = next(i for i, event in enumerate(events) if isinstance(event, CombatEnded))
    tail = events[ended_index + 1 :]
    assert [event.text for event in noted_of(tail)] == [FACT_TEXT]
    assert [(event.item_id, event.quantity) for event in granted_of(tail)] == [("v2_coin", 2)]
    assert master.state.facts.count(FACT_TEXT) == 1
    assert item_stock(master.state, "fighter", "v2_coin") == 2
    assert apply_events(base, journal.events) == master.state
    assert journal.state == master.state


# --- Победа через DM-тулу: гарантия покрывает тул-рекорд и публичные события ---


async def test_dm_tool_victory_extends_operation_commit(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 500]))
    await start_fight(master, FACT_FIGHT)
    pending = _PendingTurn((), (), {0})
    operation = trap_operation(master)

    updates = await drain(master, pending, operation)

    events = operation.outcome.events
    assert any(isinstance(event, CombatEnded) and event.winner == "party" for event in events)
    assert [event.text for event in noted_of(events)] == [FACT_TEXT]
    assert [(event.item_id, event.quantity) for event in granted_of(events)] == [("v2_coin", 2)]
    assert not master.in_combat

    record = pending.completed[-1]
    assert record["tool"] == "adjust_resource"
    assert "ItemGranted" in record["events"]
    assert "FactNoted" in record["events"]
    public_types = [event["type"] for event in record["public_events"]]
    assert "ItemGranted" in public_types
    assert "FactNoted" not in public_types

    happened = next(update for update in updates if isinstance(update, EventsHappened))
    assert [event.text for event in noted_of(list(happened.events))] == [FACT_TEXT]
    assert granted_of(list(happened.events))

    assert master.state.facts.count(FACT_TEXT) == 1
    assert item_stock(master.state, "fighter", "v2_coin") == 2
    types = [event.type for event in journal.events]
    assert types.count("CombatEnded") == 1
    assert types.count("ItemGranted") == 1
    assert types.count("FactNoted") == 1
    assert apply_events(base, journal.events) == master.state


# --- Replay из журнала идентичен snapshot для обоих путей ---


async def test_replay_identical_for_both_paths(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair

    act_master, act_journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20, 500]))
    await win_with_strike(act_master, FACT_FIGHT)
    await act_master.finish_terminal_combat_fallback()

    tool_master, tool_journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 500]))
    await start_fight(tool_master, FACT_FIGHT)
    await drain(tool_master, _PendingTurn((), (), {0}), trap_operation(tool_master))

    for master, journal in ((act_master, act_journal), (tool_master, tool_journal)):
        payloads = [JOURNAL_EVENT.dump_python(event, mode="json") for event in journal.events]
        replayed = apply_events(base, [JOURNAL_EVENT.validate_python(p) for p in payloads])
        assert replayed == master.state
        assert journal.state == master.state
        assert replayed.facts.count(FACT_TEXT) == 1
        assert item_stock(replayed, "fighter", "v2_coin") == 2

    # Тот же скрипт бросков — тот же журнал целиком (детерминизм DM-пути).
    twin, twin_journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 500]))
    await start_fight(twin, FACT_FIGHT)
    await drain(twin, _PendingTurn((), (), {0}), trap_operation(twin))
    assert [event.model_dump() for event in twin_journal.events] == [
        event.model_dump() for event in tool_journal.events
    ]


# --- Дедуп: факт не дублируется, лут не выдаётся дважды, энкаунтер одноразовый ---


async def test_victory_dedup_and_one_shot_encounter(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 500]))
    await start_fight(master, FACT_FIGHT)
    await drain(master, _PendingTurn((), (), {0}), trap_operation(master))

    assert master._tools.victory_fact_events(master.state) == []
    # DM-путь не наполняет _combat_log: fallback — no-op, журнал не пополняется.
    before = len(journal.events)
    assert await master.finish_terminal_combat_fallback() is None
    assert len(journal.events) == before
    types = [event.type for event in journal.events]
    assert types.count("FactNoted") == 1
    assert types.count("ItemGranted") == 1
    assert types.count("CombatEnded") == 1
    assert item_stock(master.state, "fighter", "v2_coin") == 2

    again = call(master, "start_combat", encounter_id=FACT_FIGHT)
    assert again.failed
    assert again.result["error"] == "encounter_already_started"
    assert apply_events(base, journal.events) == master.state


async def test_retry_after_lock_loss_does_not_duplicate(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 500]))
    await start_fight(master, FACT_FIGHT)
    pending = _PendingTurn((), (), {0})
    operation = trap_operation(master)

    async def deny() -> bool:
        return False

    before = len(journal.events)
    master.set_lock_guard(deny)
    with pytest.raises(TurnLockLostError):
        await drain(master, pending, operation)
    assert len(journal.events) == before
    assert pending.pending_operation is operation

    # Retry тем же operation: последствия уже в events, повтор не удваивает лут/факты.
    master.set_lock_guard(None)
    await drain(master, pending, operation)
    types = [event.type for event in journal.events]
    assert types.count("CombatEnded") == 1
    assert types.count("ItemGranted") == 1
    assert types.count("FactNoted") == 1
    assert master.state.facts.count(FACT_TEXT) == 1
    assert item_stock(master.state, "fighter", "v2_coin") == 2
    assert apply_events(base, journal.events) == master.state


# --- Энкаунтер без victory_facts: фактов нет, лут по правилам ---


async def test_encounter_without_victory_facts(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20, 500]))

    events = await win_with_strike(master, PLAIN_FIGHT)

    assert not noted_of(events)
    assert [(event.item_id, event.quantity) for event in granted_of(events)] == [("v2_gem", 1)]
    assert await master.finish_terminal_combat_fallback() is not None
    assert not noted_of(journal.events)
    assert not master.state.facts
    assert apply_events(base, journal.events) == master.state


async def test_facts_without_loot_table(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1]))
    await start_fight(master, FACT_ONLY_FIGHT)

    await drain(master, _PendingTurn((), (), {0}), trap_operation(master))

    assert [event.text for event in noted_of(journal.events)] == [FACT_TEXT]
    assert not granted_of(journal.events)
    assert not any(isinstance(event, Revealed) for event in journal.events)
    assert master.state.facts.count(FACT_TEXT) == 1
    assert apply_events(base, journal.events) == master.state


# --- Контент «Червей»: обязательный факт тренировочного боя объявлен ---


def test_worms_orbit_training_fact_declared(content_dir: Path) -> None:
    worms = load_game_content(content_dir, "worm-invasion-act1", "ru")
    orbit = next(entry for entry in worms.scenario.encounters if entry.id == "orbit_training_fight")
    assert orbit.victory_facts == ("training_fight_won",)
