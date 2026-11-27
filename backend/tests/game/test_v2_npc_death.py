"""Подэтап 9: смежный долг #40/#51 — смерть NPC в журнале и тула kill_npc.

Смерть NPC фиксируется событием NpcDied и восстанавливается проигрыванием журнала
(apply_events): статус durable, повторный kill_npc идемпотентен. Бой при этом НЕ
записывает смерть NPC автоматически: гибель актёра в движке правил не порождает
NpcDied и не трогает state.npc_status — это осознанный долг, зафиксированный тестом.
Без базы и сети: контент из фикстур tests/game/conftest.py, детерминированный Rng.
"""

from __future__ import annotations

import uuid

from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent
from ai_dnd.game.events import JOURNAL_EVENT
from ai_dnd.game.session import SessionSetup, SessionState, apply_events
from ai_dnd.game.tools import DmTools
from ai_dnd.rules.actions import DamageAction, StartCombatAction
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.rng import ScriptedRng, SeededRng
from ai_dnd.rules.state import Actor, Resource

PARTY = ("fighter", "rogue", "cleric")
NPC_ID = "boris"


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=uuid.uuid4().hex, name=name, arguments=dict(arguments))


def _tools(game_content: GameContent, setup: SessionSetup) -> DmTools:
    return DmTools(game_content, setup, RulesEngine(game_content.ruleset), SeededRng(7))


def test_kill_npc_emits_npc_died_and_marks_dead(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    """kill_npc выдаёт NpcDied; отсутствие ключа в npc_status означает «жив»."""
    assert any(npc.id == NPC_ID for npc in game_content.scenario.npcs)
    assert initial_state.npc_status.get(NPC_ID) is None

    outcome = _tools(game_content, setup).execute(initial_state, _call("kill_npc", npc_id=NPC_ID))

    assert not outcome.failed
    assert outcome.result == {"npc": NPC_ID, "status": "dead"}
    assert [event.type for event in outcome.events] == ["NpcDied"]
    state = apply_events(initial_state, outcome.events)
    assert state.npc_status == {NPC_ID: "dead"}
    # Прочее состояние смерть NPC не меняет.
    assert state.rules == initial_state.rules
    assert state.scene_id == initial_state.scene_id


def test_npc_death_is_durable_across_journal_replay(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    """Смерть переживает JSON-сериализацию журнала и снимка: replay восстанавливает её."""
    outcome = _tools(game_content, setup).execute(initial_state, _call("kill_npc", npc_id=NPC_ID))
    live = apply_events(initial_state, outcome.events)

    # Событие проходит через JSON как payload строки game_events.
    payloads = [JOURNAL_EVENT.dump_python(event, mode="json") for event in outcome.events]
    replayed = apply_events(initial_state, [JOURNAL_EVENT.validate_python(p) for p in payloads])
    assert replayed.model_dump(mode="json") == live.model_dump(mode="json")

    # Снимок состояния тоже round-trip'ится без потери статуса.
    restored = SessionState.model_validate(live.model_dump(mode="json"))
    assert restored.npc_status == {NPC_ID: "dead"}
    assert apply_events(restored, []).npc_status == {NPC_ID: "dead"}


def test_repeat_kill_npc_is_idempotent(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    """Повторный kill_npc мёртвого NPC: already_dead, без новых событий."""
    tools = _tools(game_content, setup)
    first = tools.execute(initial_state, _call("kill_npc", npc_id=NPC_ID))
    dead = apply_events(initial_state, first.events)

    again = tools.execute(dead, _call("kill_npc", npc_id=NPC_ID))

    assert not again.failed
    assert again.result == {"npc": NPC_ID, "already_dead": True}
    assert again.events == []
    assert apply_events(dead, again.events).model_dump(mode="json") == dead.model_dump(mode="json")


def test_kill_unknown_npc_returns_error_code(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    """Неизвестный NPC: ошибка unknown_npc со списком допустимых, журнал не меняется."""
    outcome = _tools(game_content, setup).execute(
        initial_state, _call("kill_npc", npc_id="no_such_npc")
    )
    assert outcome.failed
    assert outcome.result["error"] == "unknown_npc"
    assert NPC_ID in outcome.result["allowed"]
    assert outcome.events == []
    assert initial_state.npc_status == {}


def test_combat_death_does_not_autowrite_npc_status(
    game_content: GameContent,
    setup: SessionSetup,
    initial_state: SessionState,
    goblin: Actor,
) -> None:
    """Долг #40/#51: гибель актёра в бою не порождает NpcDied и не трогает npc_status."""
    # Актёр с id сценарного NPC: даже в этом случае движок не журналирует смерть NPC.
    npc_actor = goblin.model_copy(
        update={
            "id": NPC_ID,
            "name": "Борис",
            "resources": {"hp": Resource(current=1, max=7)},
        }
    )
    state = initial_state.model_copy(update={"rules": initial_state.rules.with_actor(npc_actor)})
    engine = RulesEngine(game_content.ruleset)
    rng = ScriptedRng([15, 15, 15, 5])
    started = engine.resolve(
        state.rules, StartCombatAction(sides={"party": PARTY, "enemies": (NPC_ID,)}), rng
    )
    rules = apply_all(state.rules, started)
    damage = engine.resolve(rules, DamageAction(target_id=NPC_ID, damage="100"), rng)
    combat_events = [*started, *damage]
    rules = apply_all(rules, damage)
    assert rules.actors[NPC_ID].dead

    after = apply_events(state, combat_events)
    assert after.rules.actors[NPC_ID].dead
    # Смерть в бою не записана в журнал как NpcDied — статус NPC остался «жив».
    assert "NpcDied" not in [event.type for event in combat_events]
    assert after.npc_status == {}

    # Тула kill_npc по-прежнему считает NPC живым и выдаёт событие смерти.
    outcome = _tools(game_content, setup).execute(after, _call("kill_npc", npc_id=NPC_ID))
    assert not outcome.failed
    assert outcome.result == {"npc": NPC_ID, "status": "dead"}
    assert [event.type for event in outcome.events] == ["NpcDied"]
    assert apply_events(after, outcome.events).npc_status == {NPC_ID: "dead"}
