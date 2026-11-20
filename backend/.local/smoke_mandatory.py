"""Разовый offline-смок двухуровневой полноты боевого нарратива (не тест, не коммитить)."""

from ai_dnd.config import Settings
from ai_dnd.game.content import load_game_content, load_party, new_session_state
from ai_dnd.game.context import ContextBuilder
from ai_dnd.game.master import GameMaster
from ai_dnd.game.render import ModelLogRenderer
from ai_dnd.game.session import SessionSetup
from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.events import (
    ActorDied,
    AttackResolved,
    ConditionApplied,
    DamageDealt,
    Healed,
)
from ai_dnd.rules.ruleset import Outcome, TurnBudget
from ai_dnd.rules.state import ActiveCondition, Actor, CombatState, Resource

content_dir = Settings(_env_file=None).content_dir
content = load_game_content(content_dir, "shadow-over-millford", "ru")
seats, actors = load_party(content_dir, content, ("fighter", "rogue", "cleric"))
setup = SessionSetup(scenario="shadow-over-millford", language="ru", seats=tuple(seats))
state = new_session_state(content, actors)

wolves = {
    f"wolf_{i}": Actor(
        id=f"wolf_{i}",
        name=f"Wolf {i}",
        kind="creature",
        resources={"hp": Resource(current=hp, max=hp)},
    )
    for i, hp in ((1, 8), (2, 9), (3, 7))
}
rules = state.rules
for wolf in wolves.values():
    rules = rules.with_actor(wolf)
# Итоговые статусы окна: fighter без сознания, rogue мёртв, wolf_1 без сознания.
rules = rules.with_actor(
    rules.actor("fighter").with_resource("hp", 0).with_conditions((ActiveCondition(id="unconscious"),))
)
rules = rules.with_actor(rules.actor("rogue").model_copy(update={"dead": True}))
rules = rules.with_actor(
    rules.actor("wolf_1").with_resource("hp", 0).with_conditions((ActiveCondition(id="unconscious"),))
)
state = state.model_copy(update={"rules": rules})


def attack(attacker: str, target: str, outcome: Outcome) -> AttackResolved:
    return AttackResolved(
        attacker_id=attacker,
        target_id=target,
        attack="strike",
        attack_name="Strike",
        outcome=outcome,
        total=15 if outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS) else 4,
        natural=12,
        target_ac=13,
        mode=RollMode.NORMAL,
    )


def damage(source: str | None, target: str, amount: int, hp_after: int) -> DamageDealt:
    return DamageDealt(
        target_id=target, source_id=source, amount=amount, rolled=amount,
        damage_type="slashing", hp_after=hp_after,
    )


window = [
    attack("fighter", "wolf_1", Outcome.SUCCESS),
    damage("fighter", "wolf_1", 5, 3),          # wounded
    attack("rogue", "wolf_2", Outcome.FAILURE),  # miss
    attack("cleric", "wolf_3", Outcome.FAILURE),  # miss
    attack("wolf_1", "fighter", Outcome.SUCCESS),
    damage("wolf_1", "fighter", 7, 0),          # downed unconscious
    ConditionApplied(actor_id="fighter", condition="unconscious", rounds=None),
    attack("wolf_2", "rogue", Outcome.SUCCESS),
    damage("wolf_2", "rogue", 9, 0),            # killed
    ActorDied(actor_id="rogue"),
    attack("wolf_3", "cleric", Outcome.CRITICAL_FAILURE),  # miss
    attack("cleric", "wolf_1", Outcome.SUCCESS),
    damage("cleric", "wolf_1", 4, 0),           # downed unconscious
    ConditionApplied(actor_id="wolf_1", condition="unconscious", rounds=None),
    Healed(target_id="cleric", source_id="cleric", amount=2, rolled=2, hp_after=10),
]

master = object.__new__(GameMaster)
master._combat_log = list(window)
master._state = state
mandatory, misses = master._mandatory_outcomes()

n = lambda aid: state.rules.actors[aid].name  # noqa: E731
expected = [
    f"{n('fighter')} → {n('wolf_1')}: 5 damage, HP 3 (wounded)",
    f"{n('wolf_1')} → {n('fighter')}: 7 damage, HP 0 (downed unconscious)",
    f"{n('wolf_2')} → {n('rogue')}: 9 damage, HP 0 (killed)",
    f"{n('rogue')} DIED",
    f"{n('cleric')} → {n('wolf_1')}: 4 damage, HP 0 (downed unconscious)",
    f"{n('cleric')} regains 2 HP, HP 10",
]
assert mandatory == expected, f"mandatory mismatch:\n{mandatory}\nvs\n{expected}"
assert misses == 3, misses
assert not any("miss" in m.lower() for m in mandatory)
assert sum("(wounded)" in m for m in mandatory) == 1
assert sum("(downed unconscious)" in m for m in mandatory) == 2
assert sum("(killed)" in m for m in mandatory) == 1
assert sum(m.endswith("DIED") for m in mandatory) == 1
assert sum("fell unconscious" in m for m in mandatory) == 0  # покрыто строками урона
assert sum("regains" in m for m in mandatory) == 1

# Потеря сознания без урона в окне — обязательна отдельной строкой.
master._combat_log = [ConditionApplied(actor_id="wolf_3", condition="unconscious", rounds=2)]
extra_mandatory, extra_misses = master._mandatory_outcomes()
assert extra_mandatory == [f"{n('wolf_3')} fell unconscious"], extra_mandatory
assert extra_misses == 0

roster = CombatState(
    round=1,
    order=("fighter", "rogue", "cleric", "wolf_1", "wolf_2", "wolf_3"),
    sides={aid: ("party" if aid in ("fighter", "rogue", "cleric") else "wolves")
           for aid in ("fighter", "rogue", "cleric", "wolf_1", "wolf_2", "wolf_3")},
    initiatives=dict.fromkeys(("fighter", "rogue", "cleric", "wolf_1", "wolf_2", "wolf_3"), 10),
    current="fighter",
    remaining=TurnBudget(),
)
renderer = ModelLogRenderer(content)
log = [line for e in window if (line := renderer.line(e, state)) is not None]
context = ContextBuilder(content, setup)
message = context.combat_message(
    state, log, roster=roster, fled=set(),
    terminal_events=["ActorDied: rogue"], ended=False,
    mandatory=mandatory, misses=misses,
)
assert "# Mandatory outcomes (each MUST be narrated individually)" in message
block = message.split("# Mandatory outcomes (each MUST be narrated individually)\n")[1]
block = block.split("\n\n# Misses in this log:")[0]
assert block == "\n".join(f"- {m}" for m in expected), block
assert "# Misses in this log: 3 (may be summarized in one phrase)" in message
for marker in (
    "NEVER a list",
    "MUST appear",
    "individually",
    "no matter how long the log is",
    "the heroes' blows all fell short",
    "ни один удар героев не достиг цели",
    "must not be described as a",
    "Do not",
    "marked DIED",
    "still alive and fighting",
    "authoritative blocks",
):
    assert marker in message, marker
assert "Describe these events vividly as one flowing scene" not in message  # старая инструкция ушла

print("mandatory outcomes:")
for m in mandatory:
    print(f"  - {m}")
print(f"misses = {misses}")
print(f"unconscious-without-damage window: {extra_mandatory}, misses={extra_misses}")
print("\n--- prompt tail (blocks + instruction) ---")
print(message[message.index("# Mandatory outcomes"):])
print("\nSMOKE OK")