"""Юнит-тесты гейта переходов между сценами (подэтап 1), кейс Ворона.

Без БД/сети/LLM: change_scene исполняется реальным DmTools через GameMaster
на «five-guardians» в памяти, журнал — MemoryJournal, LLM-заглушка падает при
любом вызове. Отряд стоит в village_night: переход по exits разрешён, не по
exits отклонён (включая обратный путь), в free-режиме гейт снят, бой блокирует
переход. Правила гейта в промпте планировщика проверяются отдельно.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.context import ContextBuilder
from ai_dnd.game.events import SceneChanged
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.game.tools import DmTools, ToolOutcome
from ai_dnd.rules.rng import Rng, ScriptedRng
from ai_dnd.scenarios.schemas import Encounter, EncounterGroup, Statblock, StatblockAttack

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
SCENE = "village_night"
EXIT = "fool_meadow"
NON_EXIT = "wolf_forest"
FIGHT = "v2_gate_fight"

BACKEND = Path(__file__).resolve().parents[2]

DUMMY_BLOCK = Statblock(
    name="Манекен",
    ac=20,
    hp=50,
    initiative=-10,
    checks={},
    attacks={"tap": StatblockAttack(name="Тычок", bonus=0, damage="1")},
)
DUMMY_FIGHT = Encounter(
    id=FIGHT, title="Пробный бой", creatures=(EncounterGroup(statblock="v2_dummy"),)
)


class NoLLM:
    async def complete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline test must not call the LLM")


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    base = load_game_content(content_dir, SCENARIO, "ru")
    scenario = base.scenario
    scenes = tuple(
        scene.model_copy(update={"encounters": (*scene.encounters, FIGHT)})
        if scene.id == SCENE
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(
        update={
            "statblocks": {**scenario.statblocks, "v2_dummy": DUMMY_BLOCK},
            "encounters": (*scenario.encounters, DUMMY_FIGHT),
            "scenes": scenes,
        }
    )
    return replace(base, scenario=patched)


@pytest.fixture(scope="session")
def exits(content: GameContent) -> tuple[str, ...]:
    scene = next(scene for scene in content.scenario.scenes if scene.id == SCENE)
    assert EXIT in scene.exits
    assert NON_EXIT not in scene.exits
    return tuple(scene.exits)


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    base = new_session_state(content, actors)
    state = base.model_copy(update={"scene_id": SCENE, "visited": (*base.visited, SCENE)})
    return setup, state


def make_master(
    content: GameContent,
    setup: SessionSetup,
    state: SessionState,
    rng: Rng,
    improvisation: str = "moderate",
) -> tuple[GameMaster, MemoryJournal]:
    session_setup = setup.model_copy(update={"improvisation": improvisation})
    journal = MemoryJournal()
    master = GameMaster(content, session_setup, state, cast(LLMGateway, NoLLM()), journal, rng)
    return master, journal


def call(master: GameMaster, name: str, **arguments: Any) -> ToolOutcome:
    tools: DmTools = master._tools
    return tools.execute(master.state, ToolCall(id="test", name=name, arguments=arguments))


async def move(master: GameMaster, scene_id: str) -> ToolOutcome:
    outcome = call(master, "change_scene", scene_id=scene_id)
    assert not outcome.failed, (scene_id, outcome.result)
    await master._commit(outcome.events)
    return outcome


# --- Переход по exits разрешён ---


async def test_exit_move_allowed(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng(()))

    outcome = await move(master, EXIT)

    assert outcome.events == [SceneChanged(scene_id=EXIT, previous=SCENE)]
    assert master.state.scene_id == EXIT
    assert EXIT in master.state.visited
    assert apply_events(base, journal.events) == master.state


async def test_one_way_exit_blocks_return(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    """fool_meadow ведёт только в patron_room: обратный путь — не exit."""
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng(()))
    await move(master, EXIT)

    outcome = call(master, "change_scene", scene_id=SCENE)

    assert outcome.result["error"] == "scene_not_reachable"
    assert outcome.result["allowed"] == ["patron_room"]
    assert not outcome.events
    assert master.state.scene_id == EXIT
    assert apply_events(base, journal.events) == master.state


# --- Переход не по exits отклонён, состояние и журнал не меняются ---


@pytest.mark.parametrize("improvisation", ["strict", "moderate"])
async def test_non_exit_rejected(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    exits: tuple[str, ...],
    improvisation: str,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng(()), improvisation)

    outcome = call(master, "change_scene", scene_id=NON_EXIT)

    assert outcome.result["error"] == "scene_not_reachable"
    assert outcome.result["allowed"] == list(exits)
    assert not outcome.events
    assert master.state.scene_id == SCENE
    assert not journal.events
    assert apply_events(base, journal.events) == master.state


async def test_free_mode_allows_non_exit(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng(()), "free")

    outcome = await move(master, NON_EXIT)

    assert outcome.events == [SceneChanged(scene_id=NON_EXIT, previous=SCENE)]
    assert master.state.scene_id == NON_EXIT
    assert apply_events(base, journal.events) == master.state


async def test_unknown_scene_rejected(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    exits: tuple[str, ...],
) -> None:
    setup, base = session_pair
    master, _ = make_master(content, setup, base, ScriptedRng(()))

    outcome = call(master, "change_scene", scene_id="v2_no_such_scene")

    assert outcome.result["error"] == "unknown_scene"
    assert outcome.result["allowed"] == list(exits)
    assert not outcome.events
    assert master.state.scene_id == SCENE


async def test_same_scene_rejected(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, _ = make_master(content, setup, base, ScriptedRng(()))

    outcome = call(master, "change_scene", scene_id=SCENE)

    assert outcome.result["error"] == "already_in_scene"
    assert not outcome.events
    assert master.state.scene_id == SCENE


# --- Бой блокирует переход даже по легальному exit ---


async def test_combat_blocks_scene_change(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1]))
    started = call(master, "start_combat", encounter_id=FIGHT)
    assert not started.failed, started.result
    await master._commit(started.events)
    assert master.in_combat

    outcome = call(master, "change_scene", scene_id=EXIT)

    assert outcome.result["error"] == "combat_in_progress"
    assert not outcome.events
    assert master.state.scene_id == SCENE
    assert apply_events(base, journal.events) == master.state


# --- Правила гейта в промпте планировщика (чистый offline-рендер) ---


def test_decision_prompt_carries_gate_rules(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, _ = session_pair
    prompt = ContextBuilder(content, setup).decision_prompt

    assert "travel ONLY to a scene listed in the current scene's Exits block" in prompt
    assert "never plan a move to a non-exit scene" in prompt
    assert "the current scene and its Exits are the ONLY reachable locations" in prompt
    assert "unreachable scenes (any other scenario scene)" in prompt
