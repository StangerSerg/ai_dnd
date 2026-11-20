"""Разовый offline-смок TPK-поражения и рестарта стола (не тест, не коммитить)."""

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.dialects import postgresql

from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.config import Settings
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.game.content import load_game_content, load_party, new_session_state
from ai_dnd.game.context import ContextBuilder
from ai_dnd.game.events import JOURNAL_EVENT, PartyDefeated
from ai_dnd.game.models import GameEventRow
from ai_dnd.game.render import EventRenderer, ModelLogRenderer
from ai_dnd.game.session import SessionSetup
from ai_dnd.game.skills import class_grants
from ai_dnd.realtime.models import SeatReadRow, SeatRow
from ai_dnd.rules.combat import party_wiped
from ai_dnd.rules.ruleset import TurnBudget
from ai_dnd.rules.state import ActiveCondition, CombatState, GameState

content_dir = Settings(_env_file=None).content_dir
content = load_game_content(content_dir, "shadow-over-millford", "ru")
seats, actors = load_party(content_dir, content, ("fighter", "rogue", "cleric"))
setup = SessionSetup(scenario="shadow-over-millford", language="ru", seats=tuple(seats))
state = new_session_state(content, actors)
party = setup.party


def down(rules: GameState, actor_id: str) -> GameState:
    actor = rules.actor(actor_id).with_resource("hp", 0)
    return rules.with_actor(actor.with_conditions((ActiveCondition(id="unconscious"),)))


def kill(rules: GameState, actor_id: str) -> GameState:
    return rules.with_actor(rules.actor(actor_id).model_copy(update={"dead": True}))


def combat_with(rules: GameState, fled: tuple[str, ...] = ()) -> GameState:
    order = tuple(rules.actors)
    return rules.model_copy(
        update={
            "combat": CombatState(
                round=1,
                order=order,
                sides={aid: "party" for aid in order},
                initiatives=dict.fromkeys(order, 10),
                current=order[0],
                remaining=TurnBudget(),
                fled=fled,
            )
        }
    )


# --- (а) party_wiped ---
rules = state.rules
all_down = down(down(down(rules, "fighter"), "rogue"), "cleric")
assert party_wiped(content.ruleset, all_down, party) is True
all_dead = kill(kill(kill(rules, "fighter"), "rogue"), "cleric")
assert party_wiped(content.ruleset, all_dead, party) is True
mixed = kill(down(rules, "fighter"), "rogue")
mixed = down(mixed, "cleric")
assert party_wiped(content.ruleset, mixed, party) is True
# Сбежавший живой герой (hp > 0, не incapacitated) — не поражение.
fled_rules = combat_with(down(down(rules, "fighter"), "cleric"), fled=("rogue",))
assert party_wiped(content.ruleset, fled_rules, party) is False
# Один на ногах — не поражение.
standing = down(down(rules, "fighter"), "rogue")
assert party_wiped(content.ruleset, standing, party) is False
assert party_wiped(content.ruleset, rules, ()) is False
print("(a) party_wiped: all_unconscious=True all_dead=True mixed=True "
      "one_fled_alive=False one_standing=False empty=False  OK")

# --- (б) combat_message: defeated ending ---
roster = CombatState(
    round=1,
    order=party,
    sides={aid: "party" for aid in party},
    initiatives=dict.fromkeys(party, 10),
    current=party[0],
    remaining=TurnBudget(),
)
context = ContextBuilder(content, setup)
kwargs = dict(roster=roster, fled=set(), terminal_events=["CombatEnded: winner=enemies"],
              mandatory=["Бранд fell unconscious"], misses=2)
defeated_msg = context.combat_message(state, ["- бой"], ended=True, defeated=True, **kwargs)
generic_msg = context.combat_message(state, ["- бой"], ended=True, defeated=False, **kwargs)
active_msg = context.combat_message(state, ["- бой"], ended=False, **kwargs)
for marker in (
    "The party has been defeated: every hero is unconscious or dead.",
    "This is the scenario's FAILURE outcome.",
    "a conclusive final phrase",
    "Do not invent a rescue, revival or reversal unless the scenario itself provides one.",
    "Keep the current scene and its terrain.",
):
    assert marker in defeated_msg, marker
assert "Describe the aftermath briefly and hand control back" not in defeated_msg
assert "Describe the aftermath briefly and hand control back to the players." in generic_msg
assert "FAILURE outcome" not in generic_msg
assert "FAILURE outcome" not in active_msg
assert "Describe the aftermath briefly" not in active_msg
# Блок полноты не тронут во всех вариантах.
for msg in (defeated_msg, generic_msg, active_msg):
    assert "# Mandatory outcomes (each MUST be narrated individually)\n- Бранд fell unconscious" in msg
    assert "# Misses in this log: 2 (may be summarized in one phrase)" in msg
print("(b) combat_message: defeated=final FAILURE instruction, ended=generic, active=empty; "
      "mandatory/misses intact  OK")

# --- render + контракт события ---
ru = EventRenderer(content, "ru").line(PartyDefeated(), state)
en = EventRenderer(content, "en").line(PartyDefeated(), state)
assert ru == "☠ Отряд пал — поражение", ru
assert en == "☠ The party has fallen — defeat", en
assert ModelLogRenderer(content).line(PartyDefeated(), state) is None
payload = JOURNAL_EVENT.dump_python(PartyDefeated(), mode="json")
assert payload == {"visibility": "public", "type": "PartyDefeated"}, payload
assert isinstance(JOURNAL_EVENT.validate_python(payload), PartyDefeated)
print(f"(render) ru={ru!r} en={en!r} model=None; wire payload={payload}  OK")

# --- (в) рестарт: пересборка начального состояния из карточек (юнит-уровень) ---
# «Побитое» состояние до рестарта.
worn = state.model_copy(update={
    "rules": all_down,
    "scene_id": content.scenario.scenes[-1].id,
    "revealed": ("secret_clue",),
    "started_encounters": ("mill_basement",),
    "summary": "что было раньше",
    "summary_through_turn": 7,
    "turn": 12,
})
# Тот же путь, что restart_session_host: карточка → build_sheet → class_grants → actor_from_sheet.
rebuilt = []
for name in ("fighter", "rogue", "cleric"):
    character = load_character(content_dir / "characters" / "ru" / f"{name}.json")
    sheet = build_sheet(character, content.template)
    granted, _ = class_grants(content.classes, content.skills, character.class_id, sheet)
    actor_id = f"pc_{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex}"  # формат pc_<hex> как в join
    rebuilt.append(actor_from_sheet(actor_id, str(sheet.values.get("name") or name), sheet, skills=granted))
fresh = new_session_state(content, rebuilt)
start = content.scenario.scenario.start_scene
public = tuple(h.id for h in content.scenario.handouts if h.visibility == "public")
assert fresh.scene_id == start and fresh.visited == (start,)
assert fresh.revealed == public
assert fresh.summary == "" and fresh.summary_through_turn == 0 and fresh.turn == 0
assert fresh.started_encounters == () and fresh.rules.combat is None
for actor in fresh.rules.actors.values():
    hp = actor.resources["hp"]
    assert hp.current == hp.max and hp.current > 0, (actor.id, hp)
    assert not actor.dead and actor.conditions == ()
# Полные хиты совпадают с исходными персонажами (сброс урона, а не пересчёт карточки).
for old, new in zip(actors, rebuilt, strict=True):
    assert old.resources["hp"].max == new.resources["hp"].max
    assert new.resources["hp"].current == new.resources["hp"].max
assert worn.rules.actor("fighter").resources["hp"].current == 0  # до рестарта был нокаут
print(f"(c) rebuild: scene={fresh.scene_id} visited={fresh.visited} revealed={fresh.revealed} "
      f"summary='' encounters=() turn=0; HP "
      + ", ".join(f"{a.id}:{a.resources['hp'].current}/{a.resources['hp'].max}" for a in rebuilt)
      + "  OK")

# --- (в) SQL очистки журнала и сброса last_seen (компиляция, без БД) ---
session_id = uuid.uuid4()
dialect = postgresql.dialect()
delete_sql = str(delete(GameEventRow).where(GameEventRow.session_id == session_id).compile(dialect=dialect))
reset_sql = str(
    update(SeatReadRow)
    .where(SeatReadRow.seat_id.in_(select(SeatRow.id).where(SeatRow.session_id == session_id)))
    .values(last_seen_seq=0)
    .compile(dialect=dialect)
)
assert "DELETE FROM game_events" in delete_sql and "game_events.session_id" in delete_sql
flat_reset = " ".join(reset_sql.split())
assert "UPDATE seat_reads SET last_seen_seq=" in flat_reset
assert "seat_reads.seat_id IN (SELECT seats.id" in flat_reset
print("(c) journal wipe SQL:", " ".join(delete_sql.split()))
print("(c) seat_reads reset SQL:", " ".join(reset_sql.split()))

# --- narrate_combat: PartyDefeated коммитится после финального Narrated только при поражении ---
import asyncio  # noqa: E402

from ai_dnd.game.master import GameMaster  # noqa: E402
from ai_dnd.rules.events import CombatEnded  # noqa: E402


def run_narration(rules: GameState) -> list[str]:
    master = object.__new__(GameMaster)
    master._content = content
    master._setup = setup
    master._state = state.model_copy(update={"rules": rules})
    master._combat_log = [CombatEnded(winner="enemies")]
    master._log_renderer = ModelLogRenderer(content)
    master._context = context
    master._pending_combat_text = "Финальный рассказ."  # LLM не вызывается
    master._combat_narration_pending = False
    master._history = []
    master._last_combat = roster
    committed: list[str] = []

    async def fake_commit(events):  # noqa: ANN001, ANN202
        committed.extend(e.type for e in events)

    master._commit = fake_commit

    async def drain() -> None:
        async for _ in master.narrate_combat():
            pass

    asyncio.run(drain())
    assert master._combat_log == []  # журнал окна очищен после коммита
    return committed


assert run_narration(combat_with(all_down)) == ["Narrated", "PartyDefeated"]
assert run_narration(combat_with(all_dead)) == ["Narrated", "PartyDefeated"]
assert run_narration(combat_with(standing)) == ["Narrated"]  # враг побеждён, отряд жив
print("(master) narrate_combat commits: defeated -> [Narrated, PartyDefeated]; "
      "victory -> [Narrated]  OK")

print("\nSMOKE OK")