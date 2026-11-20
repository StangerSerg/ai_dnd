"""Offline-смок Фазы 2 «скиллы классов»: protect/stealth на GameState+Engine+Rng.

Без БД и LLM: uv run python .local/skills_engine_smoke.py
"""

from ai_dnd.config import Settings
from ai_dnd.rules.actions import (
    AttackAction,
    DamageAction,
    EndTurnAction,
    HideAction,
    ProtectAction,
    StartCombatAction,
)
from ai_dnd.rules.dice import RollMode
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import (
    AttackResolved,
    ConditionApplied,
    ConditionRemoved,
    ProtectionEnded,
    ProtectionStarted,
    TurnSlotUsed,
)
from ai_dnd.rules.rng import SeededRng
from ai_dnd.rules.state import HIDDEN, Actor, AttackProfile, GameState, Resource
from ai_dnd.rulesets.service import load_ruleset

RULESET = load_ruleset(Settings(_env_file=None).content_dir / "rulesets" / "dnd5e-srd.json")
ENGINE = RulesEngine(RULESET)
SWORD = AttackProfile(name="sword", bonus=5, damage="1d8+3", damage_type="slashing")
CLAW = AttackProfile(name="claw", bonus=4, damage="1d6+2", damage_type="slashing")


def hero(actor_id: str, hp: int, skills: tuple[str, ...] = (), ac: int = 15) -> Actor:
    return Actor(
        id=actor_id,
        name=actor_id,
        kind="character",
        ac=ac,
        skills=skills,
        resources={"hp": Resource(current=hp, max=30)},
        attacks={"sword": SWORD},
    )


def goblin(hp: int = 20) -> Actor:
    return Actor(
        id="gob",
        name="gob",
        kind="creature",
        ac=12,
        resources={"hp": Resource(current=hp, max=20)},
        attacks={"claw": CLAW},
    )


def start(state: GameState, seed: int = 3) -> GameState:
    _, after = ENGINE.run(
        state,
        StartCombatAction(sides={"party": ("fighter", "rogue", "cleric"), "enemies": ("gob",)}),
        SeededRng(seed),
    )
    return after


def force_turn(state: GameState, actor_id: str) -> GameState:
    assert state.combat is not None
    return state.model_copy(
        update={
            "combat": state.combat.model_copy(
                update={"current": actor_id, "remaining": RULESET.combat.turn}
            )
        }
    )


def party_state() -> GameState:
    actors = [
        hero("fighter", 30, ("protect",), ac=18),
        hero("rogue", 20, ("stealth",)),
        hero("cleric", 25, ("cure_wounds",)),
        goblin(),
    ]
    return GameState(actors={actor.id: actor for actor in actors})


def check_protect_redirect() -> None:
    state = start(party_state())
    state = force_turn(state, "fighter")
    events, state = ENGINE.run(
        state, ProtectAction(actor_id="fighter", target_id="rogue"), SeededRng(1)
    )
    assert any(isinstance(e, ProtectionStarted) for e in events)
    assert any(isinstance(e, TurnSlotUsed) and e.slot == "action" for e in events)
    assert state.actor("rogue").protected_by == "fighter"
    assert state.combat is not None
    assert state.combat.remaining.action == 0

    state = force_turn(state, "gob")
    hit_seen = False
    for seed in range(60):
        events, after = ENGINE.run(
            state,
            AttackAction(attacker_id="gob", target_id="rogue", attack="claw"),
            SeededRng(seed),
        )
        resolved = next(e for e in events if isinstance(e, AttackResolved))
        assert resolved.target_id == "rogue"
        assert resolved.target_ac == 18, "бросок должен идти по AC защитника"
        assert resolved.redirected_to_id == "fighter"
        if resolved.outcome.succeeded:
            hit_seen = True
            assert after.actor("fighter").hp.current < 30, "урон получает защитник"
            assert after.actor("rogue").hp.current == 20, "исходная цель не ранена"
    assert hit_seen, "хотя бы один seed должен попасть"
    print("OK protect: AC защитника, урон защитнику, цель не ранена, action потрачен")


def check_protect_duration() -> None:
    state = start(party_state())
    state = force_turn(state, "fighter")
    _, state = ENGINE.run(
        state, ProtectAction(actor_id="fighter", target_id="cleric"), SeededRng(1)
    )
    assert state.actor("cleric").protected_by == "fighter"
    # ход доходит до защитника по очереди: cleric -> fighter
    state = force_turn(state, "cleric")
    events, state = ENGINE.run(state, EndTurnAction(actor_id="cleric"), SeededRng(1))
    assert state.combat is not None
    assert state.combat.current == "fighter"
    assert any(
        isinstance(e, ProtectionEnded) and e.reason == "expired" and e.target_id == "cleric"
        for e in events
    )
    assert state.actor("cleric").protected_by is None
    print("OK protect: прикрытие спало в начале хода защитника")


def check_protect_falls() -> None:
    state = start(party_state())
    state = force_turn(state, "fighter")
    _, state = ENGINE.run(state, ProtectAction(actor_id="fighter", target_id="rogue"), SeededRng(1))
    # защитник без сознания (0 хитов) — прикрытие спадает сразу
    events, state = ENGINE.run(state, DamageAction(target_id="fighter", damage="100"), SeededRng(1))
    assert any(isinstance(e, ProtectionEnded) and e.reason == "protector_fell" for e in events)
    assert state.actor("rogue").protected_by is None
    # удар по бывшей подопечной идёт как обычно — по её AC и по её хитам
    state = force_turn(state, "gob")
    hit_seen = False
    for seed in range(60):
        events, after = ENGINE.run(
            state,
            AttackAction(attacker_id="gob", target_id="rogue", attack="claw"),
            SeededRng(seed),
        )
        resolved = next(e for e in events if isinstance(e, AttackResolved))
        assert resolved.target_ac == 15
        assert resolved.redirected_to_id is None
        if resolved.outcome.succeeded:
            hit_seen = True
            assert after.actor("rogue").hp.current < 20
            assert after.actor("fighter").hp.current == 0
    assert hit_seen
    # погибший защитник (существо со скиллом protect) тоже снимает прикрытие
    brute = Actor(
        id="brute",
        name="brute",
        kind="creature",
        ac=16,
        skills=("protect",),
        resources={"hp": Resource(current=20, max=20)},
        attacks={"claw": CLAW},
    )
    state = GameState(actors={**party_state().actors, "brute": brute})
    _, state = ENGINE.run(
        state,
        StartCombatAction(
            sides={"party": ("fighter", "rogue", "cleric", "brute"), "enemies": ("gob",)}
        ),
        SeededRng(3),
    )
    state = force_turn(state, "brute")
    _, state = ENGINE.run(state, ProtectAction(actor_id="brute", target_id="rogue"), SeededRng(1))
    assert state.actor("rogue").protected_by == "brute"
    events, state = ENGINE.run(state, DamageAction(target_id="brute", damage="100"), SeededRng(1))
    assert state.actor("brute").dead
    assert state.actor("rogue").protected_by is None
    print("OK protect: защитник выбыл/погиб — прикрытие спало, бьют исходную цель")


def check_protect_validation() -> None:
    state = start(party_state())
    state = force_turn(state, "fighter")
    for action, code in (
        (ProtectAction(actor_id="fighter", target_id="fighter"), "cannot_protect_self"),
        (ProtectAction(actor_id="fighter", target_id="gob"), "not_an_ally"),
        (ProtectAction(actor_id="rogue", target_id="cleric"), "skill_not_available"),
    ):
        caught: str | None = None
        try:
            ENGINE.resolve(state, action, SeededRng(1))
        except ActionError as error:
            caught = error.code
        assert caught == code, (caught, code)
    print("OK protect: отказы self/enemy/no-skill")


def check_stealth() -> None:
    state = start(party_state())
    state = force_turn(state, "rogue")
    events, state = ENGINE.run(state, HideAction(actor_id="rogue"), SeededRng(1))
    assert any(isinstance(e, ConditionApplied) and e.condition == HIDDEN for e in events)
    assert state.actor("rogue").condition(HIDDEN) is not None

    # враги не таргетят скрытого
    gob_options = ENGINE.available_actions(force_turn(state, "gob"), "gob")
    assert all(getattr(o, "target_id", None) != "rogue" for o in gob_options)
    # все цели скрыты — допустимый фолбэк: скрытый снова в списке
    everyone_hidden = state
    for actor_id in ("fighter", "cleric"):
        everyone_hidden = everyone_hidden.with_actor(
            everyone_hidden.actor(actor_id).model_copy(update={"skills": ("stealth",)})
        )
    everyone_hidden = force_turn(everyone_hidden, "rogue")
    for actor_id in ("fighter", "cleric"):
        _, everyone_hidden = ENGINE.run(
            everyone_hidden, HideAction(actor_id=actor_id, slot="free"), SeededRng(1)
        )
    fallback = ENGINE.available_actions(force_turn(everyone_hidden, "gob"), "gob")
    assert any(getattr(o, "target_id", None) == "rogue" for o in fallback)

    # атака по скрытому — с помехой (free-атака вне хода)
    events, _ = ENGINE.run(
        state,
        AttackAction(attacker_id="gob", target_id="rogue", attack="claw", slot="free"),
        SeededRng(5),
    )
    resolved = next(e for e in events if isinstance(e, AttackResolved))
    assert resolved.mode is RollMode.DISADVANTAGE

    # своя атака — с преимуществом, и hidden снимается после неё
    state = force_turn(state, "rogue")
    events, state = ENGINE.run(
        state, AttackAction(attacker_id="rogue", target_id="gob", attack="sword"), SeededRng(5)
    )
    resolved = next(e for e in events if isinstance(e, AttackResolved))
    assert resolved.mode is RollMode.ADVANTAGE
    assert any(
        isinstance(e, ConditionRemoved) and e.condition == HIDDEN and e.reason == "attacked"
        for e in events
    )
    assert state.actor("rogue").condition(HIDDEN) is None
    print("OK stealth: hidden, преимущество/помеха, снятие при атаке, враги не таргетят, фолбэк")


def check_stealth_outside_combat() -> None:
    state = party_state()
    events, state = ENGINE.run(state, HideAction(actor_id="rogue"), SeededRng(1))
    assert not any(isinstance(e, TurnSlotUsed) for e in events)
    assert state.actor("rogue").condition(HIDDEN) is not None
    caught: str | None = None
    try:
        ENGINE.resolve(state, HideAction(actor_id="rogue"), SeededRng(1))
    except ActionError as error:
        caught = error.code
    assert caught == "already_hidden", caught
    print("OK stealth: вне боя без бюджета хода, повторный hide отказан")


def check_gating() -> None:
    actors = [
        hero("fighter", 30, ("protect",), ac=18),
        hero("rogue", 20, ("stealth",)),
        hero("cleric", 25, ("cure_wounds",)),
        hero("ranger", 25),
        goblin(),
    ]
    state = GameState(actors={actor.id: actor for actor in actors})
    _, state = ENGINE.run(
        state,
        StartCombatAction(
            sides={"party": ("fighter", "rogue", "cleric", "ranger"), "enemies": ("gob",)}
        ),
        SeededRng(3),
    )
    fighter = ENGINE.available_actions(force_turn(state, "fighter"), "fighter")
    rogue = ENGINE.available_actions(force_turn(state, "rogue"), "rogue")
    cleric = ENGINE.available_actions(force_turn(state, "cleric"), "cleric")
    ranger = ENGINE.available_actions(force_turn(state, "ranger"), "ranger")
    assert {o.target_id for o in fighter if isinstance(o, ProtectAction)} == {
        "rogue",
        "cleric",
        "ranger",
    }
    assert not any(isinstance(o, HideAction) for o in fighter)
    assert any(isinstance(o, HideAction) for o in rogue)
    assert not any(isinstance(o, ProtectAction) for o in rogue)
    assert not any(isinstance(o, (HideAction, ProtectAction)) for o in cleric)
    assert not any(isinstance(o, (HideAction, ProtectAction)) for o in ranger)
    # EndTurn остаётся последним вариантом (на нём держатся авто-ходы смоков)
    for options in (fighter, rogue, cleric, ranger):
        assert options[-1].type == "end_turn"
    print("OK gating: fighter=protect, rogue=hide, cleric/ranger=none")


def main() -> None:
    check_protect_redirect()
    check_protect_duration()
    check_protect_falls()
    check_protect_validation()
    check_stealth()
    check_stealth_outside_combat()
    check_gating()
    print("ALL SKILLS ENGINE SMOKES PASSED")


if __name__ == "__main__":
    main()
