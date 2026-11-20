"""Независимый offline-смок DM-инструментов protect/stealth (Root-проверка Фазы 2).

Без БД и LLM: uv run python .local/skills_dm_tools_smoke.py
"""

from ai_dnd.config import get_settings
from ai_dnd.game.content import load_game_content, load_party, new_session_state
from ai_dnd.game.session import SessionSetup, apply_event
from ai_dnd.game.tools import DmTools, ProtectArgs, StealthArgs, ToolCallError
from ai_dnd.rules.actions import StartCombatAction
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.events import ConditionApplied, ProtectionStarted, TurnSlotUsed
from ai_dnd.rules.rng import SeededRng
from ai_dnd.rules.state import HIDDEN

content_dir = get_settings().content_dir
content = load_game_content(content_dir, "five-guardians", "ru")
seats, actors = load_party(content_dir, content, ["cleric", "fighter", "rogue", "ranger"])
setup = SessionSetup(scenario="five-guardians", language="ru", seats=seats)
engine = RulesEngine(content.ruleset)
tools = DmTools(content, setup, engine, SeededRng(7))
state = new_session_state(content, actors)


def expect_error(code, fn):
    try:
        fn()
    except ToolCallError as error:
        assert error.code == code, (error.code, code)
        return
    raise AssertionError(f"expected {code}")


# protect вне боя → combat_required (основной путь — кнопка в бою).
expect_error("combat_required", lambda: tools.protect(state, ProtectArgs(protector_id="fighter")))
print("OK protect outside combat -> combat_required")

# stealth вне боя работает: скрыт, без бюджета хода.
outcome = tools.stealth(state, StealthArgs(actor_id="rogue"))
assert any(isinstance(e, ConditionApplied) and e.condition == HIDDEN for e in outcome.events)
assert not any(isinstance(e, TurnSlotUsed) for e in outcome.events)
for event in outcome.events:
    state = apply_event(state, event)
assert state.rules.actor("rogue").condition(HIDDEN) is not None
print("OK stealth outside combat -> hidden, no turn budget")

# чужой скилл → skill_not_available.
expect_error("skill_not_available", lambda: tools.stealth(state, StealthArgs(actor_id="fighter")))
expect_error(
    "skill_not_available", lambda: tools.protect(state, ProtectArgs(protector_id="rogue"))
)
print("OK wrong skill -> skill_not_available")

# Бой: добавляем монстра, стартуем, форсируем ход fighter.
from ai_dnd.rules.state import Actor, AttackProfile, Resource

gob = Actor(
    id="gob",
    name="gob",
    kind="creature",
    ac=12,
    resources={"hp": Resource(current=20, max=20)},
    attacks={"claw": AttackProfile(name="claw", bonus=4, damage="1d6+2", damage_type="slashing")},
)
rules = state.rules.model_copy(update={"actors": {**state.rules.actors, "gob": gob}})
# снимаем hidden, полученный вне боя, чтобы проверить hide в бою заново
rules = rules.with_actor(rules.actor("rogue").with_conditions(()))
events, rules = engine.run(
    rules,
    StartCombatAction(
        sides={"party": ("cleric", "fighter", "rogue", "ranger"), "enemies": ("gob",)}
    ),
    SeededRng(3),
)
state = state.model_copy(update={"rules": rules})
assert state.rules.combat is not None


def force_turn(actor_id):
    global state
    combat = state.rules.combat
    assert combat is not None
    state = state.model_copy(
        update={
            "rules": state.rules.model_copy(
                update={
                    "combat": combat.model_copy(
                        update={"current": actor_id, "remaining": content.ruleset.combat.turn}
                    )
                }
            )
        }
    )


# cleric ранен сильнее всех → дефолтная цель protect.
cleric = state.rules.actor("cleric")
state = state.model_copy(
    update={
        "rules": state.rules.with_actor(
            cleric.with_resource("hp", 5)
        )
    }
)
force_turn("fighter")
outcome = tools.protect(state, ProtectArgs(protector_id="fighter"))
assert outcome.result["target_id"] == "cleric", outcome.result
assert any(isinstance(e, ProtectionStarted) for e in outcome.events)
assert any(isinstance(e, TurnSlotUsed) and e.slot == "action" for e in outcome.events)
for event in outcome.events:
    state = apply_event(state, event)
assert state.rules.actor("cleric").protected_by == "fighter"
print("OK protect default -> most wounded standing ally (cleric, 5 hp)")

# protect по имени: новый ход fighter, цель rogue.
force_turn("fighter")
outcome = tools.protect(
    state, ProtectArgs(protector_id="fighter", target_name=state.rules.actor("rogue").name)
)
assert outcome.result["target_id"] == "rogue", outcome.result
print("OK protect by name -> rogue")

# невалидная цель.
expect_error(
    "invalid_protect_target",
    lambda: tools.protect(state, ProtectArgs(protector_id="fighter", target_id="gob")),
)
print("OK protect invalid target -> invalid_protect_target")

# stealth в бою в ход rogue: тратит действие, даёт hidden.
force_turn("rogue")
outcome = tools.stealth(state, StealthArgs(actor_id="rogue"))
assert any(isinstance(e, TurnSlotUsed) and e.slot == "action" for e in outcome.events)
assert any(isinstance(e, ConditionApplied) and e.condition == HIDDEN for e in outcome.events)
print("OK stealth in combat -> TurnSlotUsed + hidden")

print("ALL DM TOOLS SMOKES PASSED")