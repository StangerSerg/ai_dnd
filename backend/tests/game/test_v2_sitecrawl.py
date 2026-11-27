"""Юнит-тесты случайных находок в чеках сцены (подэтап 6b, сайт-кроул).

Без БД/сети/LLM: проверки идут через реальный GameMaster/DmTools на копии
сценария «five-guardians» в памяти, броски заданы ScriptedRng (d20 проверки и
выбор находки детерминированы), LLM-заглушка падает при любом вызове.
Проверяются одна случайная находка из пула on_success.reveal, гарантированная
находка из одного id, исчерпание пула, запрет повторной находки при наличии
предмета у любого члена отряда, улики без предметов, отсутствие находок при
провале, находка на спасброске и идентичность replay.
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
from ai_dnd.rules.events import CheckResolved
from ai_dnd.rules.rng import Rng, ScriptedRng, SeededRng
from ai_dnd.scenarios.schemas import CheckResult, Item, SceneCheck
from ai_dnd.scenarios.service import find_scenario_issues

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
START_SCENE = "tavern"
SEARCH_MANY = "v2_search_many"
SEARCH_ONE = "v2_search_one"
SEARCH_CLUE = "v2_search_clue"
SEARCH_HARD = "v2_search_hard"
SAVE_FIND = "v2_save_find"

BACKEND = Path(__file__).resolve().parents[2]
PROMPTS = BACKEND / "src" / "ai_dnd" / "game" / "prompts"

RELIC_A = Item(id="v2_relic_a", name="Реликвия А", quantity=2)
RELIC_B = Item(id="v2_relic_b", name="Реликвия Б")
RELIC_C = Item(id="v2_relic_c", name="Реликвия В", quantity=3)
FIND_ITEMS = (RELIC_A, RELIC_B, RELIC_C)
FIND_IDS = tuple(item.id for item in FIND_ITEMS)

FIXTURE_CHECKS = (
    SceneCheck(
        id=SEARCH_MANY,
        description="Осмотр руин: три находки в списке.",
        skill="perception",
        dc=2,
        on_success=CheckResult(reveal=("clue_patron_odd", "guardian_truth", *FIND_IDS)),
        on_failure=CheckResult(text="Ничего примечательного."),
    ),
    SceneCheck(
        id=SEARCH_ONE,
        description="Осмотр тайника: одна гарантированная находка.",
        skill="perception",
        dc=2,
        on_success=CheckResult(reveal=("v2_relic_a",)),
    ),
    SceneCheck(
        id=SEARCH_CLUE,
        description="Осмотр без предметов: только улика.",
        skill="insight",
        dc=2,
        on_success=CheckResult(reveal=("clue_patron_displeased",)),
    ),
    SceneCheck(
        id=SEARCH_HARD,
        description="Почти невозможный осмотр.",
        skill="perception",
        dc=30,
        on_success=CheckResult(reveal=FIND_IDS),
        on_failure=CheckResult(reveal=("clue_dark_creatures", "v2_relic_b")),
    ),
    SceneCheck(
        id=SAVE_FIND,
        description="Спасбросок с находкой при успехе.",
        save="dex",
        dc=2,
        on_success=CheckResult(reveal=("v2_relic_c",)),
    ),
)


class NoLLM:
    async def complete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline test must not call the LLM")


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    base = load_game_content(content_dir, SCENARIO, "ru")
    scenario = base.scenario
    scenes = tuple(
        scene.model_copy(
            update={
                "checks": (*scene.checks, *FIXTURE_CHECKS),
                "items": (*scene.items, *FIND_IDS),
            }
        )
        if scene.id == START_SCENE
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(
        update={"items": (*scenario.items, *FIND_ITEMS), "scenes": scenes}
    )
    return replace(base, scenario=patched)


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    return setup, new_session_state(content, actors)


def make_master(
    content: GameContent, setup: SessionSetup, state: SessionState, rng: Rng
) -> tuple[GameMaster, MemoryJournal]:
    journal = MemoryJournal()
    return GameMaster(content, setup, state, cast(LLMGateway, NoLLM()), journal, rng), journal


def call(master: GameMaster, name: str, **arguments: Any) -> ToolOutcome:
    return master._tools.execute(master.state, ToolCall(id="test", name=name, arguments=arguments))


async def roll(
    master: GameMaster, check_id: str, *, save: bool = False, actor: str = "fighter"
) -> ToolOutcome:
    name = "request_save" if save else "request_check"
    outcome = call(master, name, actor_id=actor, scene_check=check_id)
    assert not outcome.failed, (check_id, outcome.result)
    await master._commit(outcome.events)
    return outcome


def granted_of(events: list) -> list[ItemGranted]:
    return [event for event in events if isinstance(event, ItemGranted)]


def assert_find_order(events: list) -> None:
    """CheckResolved -> Revealed предмета -> ItemGranted, ровно один Revealed на предмет."""
    resolved = next(i for i, event in enumerate(events) if isinstance(event, CheckResolved))
    for index, event in enumerate(events):
        if not isinstance(event, ItemGranted):
            continue
        assert resolved < index
        reveals = [
            i
            for i, other in enumerate(events)
            if isinstance(other, Revealed) and other.entity_id == event.item_id
        ]
        assert len(reveals) == 1
        assert reveals[0] < index


# --- Одна случайная находка из пула, детерминированная выбором rng ---


@pytest.mark.parametrize("index", [0, 1, 2])
async def test_random_find_from_pool(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    index: int,
) -> None:
    setup, base = session_pair
    # d20=10 (успех при dc 2), затем choice из трёх кандидатов индексом index.
    master, journal = make_master(content, setup, base, ScriptedRng([10, index]))

    outcome = await roll(master, SEARCH_MANY)

    assert outcome.result["succeeded"]
    grants = granted_of(outcome.events)
    assert len(grants) == 1
    grant = grants[0]
    assert grant.item_id == FIND_IDS[index]
    assert grant.actor_id == "fighter"
    assert grant.quantity == FIND_ITEMS[index].quantity
    assert outcome.result["found"]["id"] == grant.item_id
    assert item_stock(master.state, "fighter", grant.item_id) == FIND_ITEMS[index].quantity
    assert master.state.is_revealed(grant.item_id)
    assert_find_order(outcome.events)
    # Улика и раздатка открываются все; предмет идёт через found, не через revealed.
    assert master.state.is_revealed("clue_patron_odd")
    assert master.state.is_revealed("guardian_truth")
    revealed_ids = {entry["id"] for entry in outcome.result["revealed"]}
    assert {"clue_patron_odd", "guardian_truth"} <= revealed_ids
    assert grant.item_id not in revealed_ids
    assert apply_events(base, journal.events) == master.state


async def test_single_id_is_guaranteed(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    rng = ScriptedRng([10])
    master, journal = make_master(content, setup, base, rng)

    outcome = await roll(master, SEARCH_ONE)

    grants = granted_of(outcome.events)
    assert len(grants) == 1
    assert grants[0].item_id == "v2_relic_a"
    assert grants[0].quantity == 2
    assert item_stock(master.state, "fighter", "v2_relic_a") == 2
    # Единственный кандидат не тратит бросок: скрипт исчерпан ровно проверкой.
    assert rng.remaining == 0
    assert apply_events(base, journal.events) == master.state


async def test_repeat_searches_exhaust_pool(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    # Успехи 10/10/10/10; выбор 0 из [a,b,c], 0 из [b,c]; третий — единственный,
    # четвёртый — пустой пул (бросок выбора не тратится).
    rng = ScriptedRng([10, 0, 10, 0, 10, 10])
    master, journal = make_master(content, setup, base, rng)

    found: list[str] = []
    for attempt in range(4):
        outcome = await roll(master, SEARCH_MANY)
        assert outcome.result["succeeded"]
        grants = granted_of(outcome.events)
        if attempt < 3:
            assert len(grants) == 1
            assert grants[0].item_id not in found
            found.append(grants[0].item_id)
        else:
            assert not grants
            assert "found" not in outcome.result
    assert sorted(found) == sorted(FIND_IDS)
    assert rng.remaining == 0
    clue_reveals = [
        event
        for event in journal.events
        if isinstance(event, Revealed) and event.entity_id == "clue_patron_odd"
    ]
    assert len(clue_reveals) == 1
    assert apply_events(base, journal.events) == master.state


async def test_party_wide_stock_blocks_find(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([10]))
    granted = call(master, "grant_item", actor_id="cleric", item_id="v2_relic_a")
    assert not granted.failed, granted.result
    await master._commit(granted.events)
    assert item_stock(master.state, "cleric", "v2_relic_a") == 2

    outcome = await roll(master, SEARCH_ONE)

    assert outcome.result["succeeded"]
    assert not granted_of(outcome.events)
    assert "found" not in outcome.result
    assert item_stock(master.state, "fighter", "v2_relic_a") == 0
    assert apply_events(base, journal.events) == master.state


# --- Улики без предметов, провал и спасбросок ---


async def test_clue_only_check(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([10]))

    outcome = await roll(master, SEARCH_CLUE)

    assert not granted_of(outcome.events)
    assert "found" not in outcome.result
    assert [entry["id"] for entry in outcome.result["revealed"]] == ["clue_patron_displeased"]
    assert master.state.is_revealed("clue_patron_displeased")
    assert apply_events(base, journal.events) == master.state


async def test_failed_check_grants_nothing(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([2]))

    outcome = await roll(master, SEARCH_HARD)

    assert not outcome.result["succeeded"]
    assert not granted_of(outcome.events)
    assert "found" not in outcome.result
    # Улика из on_failure.reveal открывается; предмет из того же списка — нет.
    assert master.state.is_revealed("clue_dark_creatures")
    assert not master.state.is_revealed("v2_relic_b")
    assert not granted_of(journal.events)
    assert apply_events(base, journal.events) == master.state


async def test_save_grants_find(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([10]))

    outcome = await roll(master, SAVE_FIND, save=True)

    assert outcome.result["succeeded"]
    grants = granted_of(outcome.events)
    assert len(grants) == 1
    assert grants[0].item_id == "v2_relic_c"
    assert grants[0].quantity == 3
    assert_find_order(outcome.events)
    assert apply_events(base, journal.events) == master.state


async def test_same_seed_same_journal(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, SeededRng(11))
    twin, twin_journal = make_master(content, setup, base, SeededRng(11))

    # Одинаковый seed даёт одинаковую последовательность: успех на одной и той же попытке.
    for _ in range(20):
        first = await roll(master, SEARCH_MANY)
        second = await roll(twin, SEARCH_MANY)
        assert first.result["succeeded"] == second.result["succeeded"]
        if first.result["succeeded"]:
            break
    else:
        raise AssertionError("no successful roll within 20 attempts")

    assert [event.model_dump() for event in twin_journal.events] == [
        event.model_dump() for event in journal.events
    ]
    assert granted_of(first.events)[0].item_id == granted_of(second.events)[0].item_id


# --- Валидация находок и правила в промптах ---


def test_reveal_validation(content_dir: Path, content: GameContent) -> None:
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

    broken_check = FIXTURE_CHECKS[0].model_copy(
        update={"on_success": CheckResult(reveal=("v2_missing",))}
    )
    broken = fixture.model_copy(
        update={
            "scenes": tuple(
                scene.model_copy(
                    update={
                        "checks": tuple(
                            broken_check if check.id == SEARCH_MANY else check
                            for check in scene.checks
                        )
                    }
                )
                if scene.id == START_SCENE
                else scene
                for scene in fixture.scenes
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
        f"scenes.{START_SCENE}.checks.{SEARCH_MANY}.on_success.reveal[0]: unknown_reveal" in issue
        for issue in issues
    )


def test_prompts_carry_random_find_rule() -> None:
    dm = (PROMPTS / "dm_system.j2").read_text(encoding="utf-8")
    decision = (PROMPTS / "decision_system.j2").read_text(encoding="utf-8")
    assert "grants one random not-yet-found item automatically" in dm
    assert "never grant it again with grant_item" in dm
    assert "never plan grant_item for such a find" in decision
