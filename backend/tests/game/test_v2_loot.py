"""Юнит-тесты лута/дропа после победы (подэтап 6).

Без БД/сети/LLM: бой идёт через реальный GameMaster/DmTools на копии сценария
«five-guardians» в памяти, броски заданы ScriptedRng (инициатива, атака и
пер-милле броски шанса детерминированы), LLM-заглушка падает при любом вызове.
Проверяются ItemGranted+Revealed после CombatEnded тем же коммитом, порядок и
публичная видимость событий, round-robin получателей, chance-дроп, отсутствие
лута при пустой таблице/поражении/бегстве и идентичность replay.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import ItemGranted, Revealed
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import SessionSetup, SessionState, apply_events, item_stock
from ai_dnd.game.tools import ToolOutcome
from ai_dnd.rules.actions import AttackAction, EndTurnAction, FleeAction
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import CombatEnded
from ai_dnd.rules.rng import Rng, ScriptedRng
from ai_dnd.rules.state import AttackProfile
from ai_dnd.scenarios.schemas import (
    Encounter,
    EncounterGroup,
    Item,
    LootEntry,
    Statblock,
    StatblockAttack,
)
from ai_dnd.scenarios.service import find_scenario_issues

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
WEAK = "v2_weakling"
BRUTE = "v2_brute"
LOOT_FIGHT = "v2_loot_fight"
EMPTY_FIGHT = "v2_empty_fight"
BRUTE_FIGHT = "v2_brute_fight"
CHANCE_FIGHT = "v2_chance_fight"

BACKEND = Path(__file__).resolve().parents[2]
DM_PROMPT = BACKEND / "src" / "ai_dnd" / "game" / "prompts" / "dm_system.j2"

COIN = Item(id="v2_coin", name="Монеты добычи", quantity=5)
GEM = Item(id="v2_gem", name="Самоцвет добычи")
CHARM = Item(id="v2_charm", name="Оберег добычи")

WEAKLING = Statblock(
    name="Слабак",
    ac=1,
    hp=1,
    initiative=-10,
    checks={},
    attacks={"claw": StatblockAttack(name="Удар", bonus=0, damage="1")},
)
BRUTE_BLOCK = Statblock(
    name="Громила",
    ac=20,
    hp=50,
    initiative=10,
    checks={},
    attacks={"smash": StatblockAttack(name="Удар", bonus=50, damage="100")},
)
ENCOUNTERS = (
    Encounter(
        id=LOOT_FIGHT,
        title="Бой с добычей",
        creatures=(EncounterGroup(statblock=WEAK),),
        loot=(
            LootEntry(item_id="v2_coin", quantity=2),
            LootEntry(item_id="v2_gem"),
            LootEntry(item_id="v2_charm", chance=0.5),
        ),
    ),
    Encounter(id=EMPTY_FIGHT, title="Бой без добычи", creatures=(EncounterGroup(statblock=WEAK),)),
    Encounter(
        id=BRUTE_FIGHT,
        title="Бой с громиллой",
        creatures=(EncounterGroup(statblock=BRUTE),),
        loot=(LootEntry(item_id="v2_coin"),),
    ),
    Encounter(
        id=CHANCE_FIGHT,
        title="Бой с редким дропом",
        creatures=(EncounterGroup(statblock=WEAK),),
        loot=(LootEntry(item_id="v2_charm", chance=0.05),),
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
            "items": (*scenario.items, COIN, GEM, CHARM),
            "statblocks": {**scenario.statblocks, WEAK: WEAKLING, BRUTE: BRUTE_BLOCK},
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


async def win_with_strike(master: GameMaster, encounter_id: str) -> list:
    """Инициатива и попадание заданы скриптом: воин убивает слабака первым ударом."""
    await start_fight(master, encounter_id)
    assert master.current_combatant == "fighter"
    events = await master.act(AttackAction(attacker_id="fighter", target_id=WEAK, attack="strike"))
    ended = next(event for event in events if isinstance(event, CombatEnded))
    assert ended.winner == "party"
    assert not master.in_combat
    return events


def granted_of(events: list) -> list[ItemGranted]:
    return [event for event in events if isinstance(event, ItemGranted)]


# --- Победа: лут тем же коммитом после CombatEnded, порядок и видимость ---


async def test_victory_loot_order_and_visibility(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    # Инициатива 20/10/1, атака 20, шанс-броски 500/500/400: выпадает всё.
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20, 500, 500, 400]))

    events = await win_with_strike(master, LOOT_FIGHT)

    ended_index = next(i for i, event in enumerate(events) if isinstance(event, CombatEnded))
    loot = events[ended_index + 1 :]
    assert all(event.visibility == "public" for event in loot)
    granted = granted_of(loot)
    by_item = {event.item_id: event for event in granted}
    assert by_item["v2_coin"].actor_id == "fighter"
    assert by_item["v2_coin"].quantity == 2
    assert by_item["v2_gem"].actor_id == "cleric"
    assert by_item["v2_gem"].quantity == 1
    assert by_item["v2_charm"].actor_id == "fighter"
    revealed = [event for event in loot if isinstance(event, Revealed)]
    assert len(revealed) == len(granted) == 3
    for grant in granted:
        rev = next(event for event in revealed if event.entity_id == grant.item_id)
        assert rev.kind == "item"
        assert loot.index(rev) < loot.index(grant)
    assert item_stock(master.state, "fighter", "v2_coin") == 2
    assert item_stock(master.state, "cleric", "v2_gem") == 1
    assert master.state.is_revealed("v2_coin")
    assert master.state.is_revealed("v2_gem")

    # Финальная наррация без LLM: ItemGranted строго до Narrated, повтор ничего не пишет.
    assert master.round_finished
    assert await master.finish_terminal_combat_fallback() is not None
    types = [event.type for event in journal.events]
    assert types.index("ItemGranted") < types.index("Narrated")
    assert types.count("CombatEnded") == 1
    before = len(journal.events)
    assert await master.finish_terminal_combat_fallback() is None
    with pytest.raises(ActionError):
        await master.act(EndTurnAction(actor_id="fighter"))
    assert len(journal.events) == before
    assert apply_events(base, journal.events) == master.state

    # Тот же скрипт бросков — тот же журнал целиком.
    twin, twin_journal = make_master(
        content, setup, base, ScriptedRng([20, 10, 1, 20, 500, 500, 400])
    )
    await win_with_strike(twin, LOOT_FIGHT)
    await twin.finish_terminal_combat_fallback()
    assert [event.model_dump() for event in twin_journal.events] == [
        event.model_dump() for event in journal.events
    ]


# --- chance-дроп: пер-милле бросок детерминирован и воспроизводим ---


@pytest.mark.parametrize(
    ("encounter_id", "roll", "dropped"),
    [
        (LOOT_FIGHT, 500, True),
        (LOOT_FIGHT, 501, False),
        (CHANCE_FIGHT, 50, True),
        (CHANCE_FIGHT, 51, False),
    ],
)
async def test_chance_drop_boundaries(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    encounter_id: str,
    roll: int,
    dropped: bool,
) -> None:
    setup, base = session_pair
    tail = [500, 500, roll] if encounter_id == LOOT_FIGHT else [roll]
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20, *tail]))

    events = await win_with_strike(master, encounter_id)

    charm = [event for event in granted_of(events) if event.item_id == "v2_charm"]
    assert bool(charm) is dropped
    if dropped:
        assert charm[0].quantity == 1
        assert item_stock(master.state, "fighter", "v2_charm") == 1
    else:
        assert item_stock(master.state, "fighter", "v2_charm") == 0
        assert not master.state.is_revealed("v2_charm")
    assert apply_events(base, journal.events) == master.state


# --- Пустая таблица, поражение и бегство лута не дают ---


async def test_empty_loot_table_grants_nothing(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 20]))

    events = await win_with_strike(master, EMPTY_FIGHT)

    assert not [event for event in events if isinstance(event, ItemGranted | Revealed)]
    await master.finish_terminal_combat_fallback()
    assert not granted_of(journal.events)
    assert apply_events(base, journal.events) == master.state


async def test_defeat_grants_nothing(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    # Инициатива 1/2/20: громила ходит первым и убивает обоих бросками 20.
    master, journal = make_master(content, setup, base, ScriptedRng([1, 2, 20, 20, 20]))
    await start_fight(master, BRUTE_FIGHT)

    for _ in range(30):
        if not master.in_combat:
            break
        current = master.current_combatant
        assert current is not None
        if master.is_party(current):
            await master.act(EndTurnAction(actor_id=current))
        else:
            await master.run_creature_turns()

    assert not master.in_combat
    ended = next(event for event in journal.events if isinstance(event, CombatEnded))
    assert ended.winner == "enemies"
    assert not granted_of(journal.events)
    assert await master.finish_terminal_combat_fallback() is not None
    assert "PartyDefeated" in [event.type for event in journal.events]
    assert apply_events(base, journal.events) == master.state


async def test_flee_grants_nothing(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1]))
    await start_fight(master, LOOT_FIGHT)

    await master.act(FleeAction(actor_id="fighter"))
    assert master.current_combatant == "cleric"
    await master.act(FleeAction(actor_id="cleric"))

    assert not master.in_combat
    ended = next(event for event in journal.events if isinstance(event, CombatEnded))
    assert ended.winner != "party"
    assert not granted_of(journal.events)
    assert apply_events(base, journal.events) == master.state


# --- Контент и валидация таблицы дропа (content/ только читается) ---


def test_content_wolf_loot_unchanged(base_content: GameContent) -> None:
    encounters = {entry.id: entry for entry in base_content.scenario.encounters}
    for encounter_id in ("wolf_pack", "wolf_den"):
        assert encounters[encounter_id].loot == (LootEntry(item_id="wolf_tails", quantity=5),)
    assert all(
        not entry.loot
        for entry in base_content.scenario.encounters
        if entry.id not in ("wolf_pack", "wolf_den")
    )


def test_loot_validation(content_dir: Path, content: GameContent) -> None:
    fixture = content.scenario
    assert (
        find_scenario_issues(
            fixture,
            content.bestiaries,
            content.ruleset,
            classes=content.classes,
            content_dir=content_dir,
        )
        == []
    )

    broken_encounter = next(entry for entry in fixture.encounters if entry.id == LOOT_FIGHT)
    broken = fixture.model_copy(
        update={
            "encounters": tuple(
                broken_encounter.model_copy(update={"loot": (LootEntry(item_id="v2_missing"),)})
                if entry.id == LOOT_FIGHT
                else entry
                for entry in fixture.encounters
            )
        }
    )
    issues = [
        str(issue)
        for issue in find_scenario_issues(
            broken,
            content.bestiaries,
            content.ruleset,
            classes=content.classes,
            content_dir=content_dir,
        )
    ]
    assert any(
        f"encounters.{LOOT_FIGHT}.loot[0].item_id: unknown_item" in issue for issue in issues
    )


def test_prompt_carries_autoloot_rule() -> None:
    template = DM_PROMPT.read_text(encoding="utf-8")
    assert "Victory loot is granted automatically by the mechanics" in template
    assert "never grant that loot again with grant_item" in template
