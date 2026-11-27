"""Юнит-тесты сценарных эпитетов урона (wound_epithets), только offline-логика.

Без БД/сети/LLM: реальные GameMaster/ContextBuilder, MemoryJournal, SeededRng;
урон наносится движком по мишеням с 100 HP (доли 0.05/0.20/0.40/0.60).
Проверяются нативные ярлыки «Червей» («повреждение», без «рана/ранение») и
дефолты five-guardians, канонические английские ключи в mandatory-списке master
(аудит-контракт), пример инструкции DM из эффективных эпитетов и запрет «6 ран»,
валидация (unknown_wound_epithet, пустые/пробельные значения) и неизменность
событий/replay при переопределении ярлыков.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from pydantic import ValidationError

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.rules.actions import DamageAction, StartCombatAction
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.rng import SeededRng
from ai_dnd.rules.state import HIT_POINTS, Actor, Resource
from ai_dnd.scenarios.schemas import ScenarioInfo
from ai_dnd.scenarios.service import find_scenario_issues

WORMS = "worm-invasion-act1"
GUARDIANS = "five-guardians"
PARTIES = {WORMS: ("ship_striker",), GUARDIANS: ("fighter",)}
CANONICAL = ("light wound", "serious wound", "heavy wound", "devastating blow")
# Доли max HP: 0.05, 0.20, 0.40, 0.60 -> light / serious / heavy / devastating.
DAMAGE = (5, 20, 40, 60)


class NoLLM:
    async def complete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline test must not call the LLM")


@pytest.fixture(scope="session")
def contents(content_dir: Path) -> dict[str, GameContent]:
    return {slug: load_game_content(content_dir, slug, "ru") for slug in PARTIES}


async def make_probe(
    content_dir: Path, content: GameContent, slug: str
) -> tuple[GameMaster, MemoryJournal, SessionState]:
    """Бой без LLM: четыре мишени по 100 HP получают урон четырёх тяжестей."""
    seats, actors = load_party(content_dir, content, PARTIES[slug])
    setup = SessionSetup(scenario=slug, language="ru", seats=tuple(seats))
    base = new_session_state(content, actors)
    enemies = tuple(
        Actor(
            id=f"target_{i}",
            name=f"Мишень {i}",
            kind="creature",
            ac=10,
            resources={HIT_POINTS: Resource(current=100, max=100)},
        )
        for i in range(1, 5)
    )
    rules = base.rules
    for enemy in enemies:
        rules = rules.with_actor(enemy)
    state = base.model_copy(update={"rules": rules})
    journal = MemoryJournal()
    master = GameMaster(content, setup, state, cast(LLMGateway, NoLLM()), journal, SeededRng(7))

    rng = SeededRng(7)
    events = list(
        master.engine.resolve(
            state.rules,
            StartCombatAction(
                sides={"party": setup.party, "enemies": tuple(enemy.id for enemy in enemies)}
            ),
            rng,
        )
    )
    live_rules = apply_all(state.rules, events)
    for enemy, amount in zip(enemies, DAMAGE, strict=True):
        hit = master.engine.resolve(
            live_rules,
            DamageAction(target_id=enemy.id, damage=str(amount), source_id=setup.party[0]),
            rng,
        )
        live_rules = apply_all(live_rules, hit)
        events.extend(hit)
    await master._commit(events)
    master._combat_log.extend(events)
    return master, journal, state


def combat_message(master: GameMaster) -> str:
    mandatory, misses = master._mandatory_outcomes()
    lines = [
        line
        for event in master._combat_log
        if (line := master._log_renderer.line(event, master.state)) is not None
    ]
    roster = master.state.rules.combat
    assert roster is not None
    return master._context.combat_message(
        master.state,
        lines,
        roster=roster,
        fled=set(),
        terminal_events=[],
        ended=False,
        mandatory=mandatory,
        misses=misses,
    )


def checklist_of(message: str) -> str:
    start = message.index("# Mandatory outcomes (each MUST be narrated individually)")
    return message[start : message.index("# Misses in this log")]


# --- Чек-лист: нативные ярлыки «Червей» и дефолты five-guardians ---


@pytest.mark.parametrize(
    ("slug", "labels", "forbidden"),
    [
        (
            WORMS,
            (
                "лёгкое повреждение",
                "серьёзное повреждение",
                "тяжёлое повреждение",
                "сокрушительный удар",
            ),
            ("ранение", "рана"),
        ),
        (
            GUARDIANS,
            ("лёгкая рана", "серьёзное ранение", "тяжёлое ранение", "сокрушительный удар"),
            (),
        ),
    ],
)
async def test_checklist_renders_effective_epithets(
    content_dir: Path,
    contents: dict[str, GameContent],
    slug: str,
    labels: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    master, _, _ = await make_probe(content_dir, contents[slug], slug)
    checklist = checklist_of(combat_message(master))
    for label in labels:
        assert label in checklist, label
    for word in forbidden:
        assert word not in checklist, word


# --- Аудит-контракт: канонические английские ключи остаются в mandatory master ---


@pytest.mark.parametrize("slug", [WORMS, GUARDIANS])
async def test_mandatory_keeps_canonical_keys(
    content_dir: Path, contents: dict[str, GameContent], slug: str
) -> None:
    master, _, _ = await make_probe(content_dir, contents[slug], slug)
    mandatory, _ = master._mandatory_outcomes()
    joined = "\n".join(mandatory)
    for key in CANONICAL:
        assert f": {key} (" in joined, key


# --- Инструкция DM: пример из эффективных эпитетов, запрет «6 ран» сохранён ---


@pytest.mark.parametrize("slug", [WORMS, GUARDIANS])
async def test_dm_instruction_example_uses_effective_epithets(
    content_dir: Path, contents: dict[str, GameContent], slug: str
) -> None:
    master, _, _ = await make_probe(content_dir, contents[slug], slug)
    message = combat_message(master)
    example = message[message.index("(e.g. «") : message.index("»)") + 2]
    if slug == WORMS:
        assert "повреждение" in example
        assert "ранение" not in example
    else:
        assert "ранение" in example
    assert "«6 ран»" in message


# --- Валидация: неизвестный ключ и пустые/пробельные значения ---


@pytest.mark.parametrize("slug", [WORMS, GUARDIANS])
def test_unknown_wound_epithet_issue(
    content_dir: Path, contents: dict[str, GameContent], slug: str
) -> None:
    content = contents[slug]
    bad_info = content.scenario.scenario.model_copy(
        update={"wound_epithets": {"bleeding": "кровотечение"}}
    )
    bad = content.scenario.model_copy(update={"scenario": bad_info})
    issues = find_scenario_issues(
        bad,
        content.bestiaries,
        content.ruleset,
        classes=content.classes,
        content_dir=content_dir,
    )
    assert any(
        issue.code == "unknown_wound_epithet" and issue.path == "scenario.wound_epithets.bleeding"
        for issue in issues
    )


@pytest.mark.parametrize("bad_value", ["", "   ", 5, None])
def test_schema_rejects_blank_epithet_values(
    contents: dict[str, GameContent], bad_value: Any
) -> None:
    info_dump = contents[WORMS].scenario.scenario.model_dump()
    with pytest.raises(ValidationError):
        ScenarioInfo.model_validate({**info_dump, "wound_epithets": {"wound": bad_value}})


# --- Replay: переопределение ярлыков не меняет события и snapshot ---


async def test_replay_unchanged_by_epithet_override(
    content_dir: Path, contents: dict[str, GameContent]
) -> None:
    content = contents[WORMS]
    overridden = replace(
        content,
        scenario=content.scenario.model_copy(
            update={
                "scenario": content.scenario.scenario.model_copy(
                    update={
                        "wound_epithets": {
                            "light wound": "царапина",
                            "serious wound": "пролом",
                        }
                    }
                )
            }
        ),
    )
    native_master, native_journal, native_base = await make_probe(content_dir, content, WORMS)
    other_master, other_journal, other_base = await make_probe(content_dir, overridden, WORMS)

    assert [event.model_dump() for event in native_journal.events] == [
        event.model_dump() for event in other_journal.events
    ]
    for master, journal, base in (
        (native_master, native_journal, native_base),
        (other_master, other_journal, other_base),
    ):
        assert apply_events(base, journal.events) == master.state
        assert journal.state == master.state

    # Меняется только рендер: нативный и переопределённый чек-листы различаются.
    native_checklist = checklist_of(combat_message(native_master))
    other_checklist = checklist_of(combat_message(other_master))
    assert "лёгкое повреждение" in native_checklist
    assert "царапина" in other_checklist
    assert "царапина" not in native_checklist
