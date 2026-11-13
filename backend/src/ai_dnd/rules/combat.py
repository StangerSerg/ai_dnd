"""Бой: инициатива, очередь ходов, раунды и то, что можно сделать за ход
(архитектура, раздел 5.1).

Модель упрощённая. За ход у участника есть действие, бонусное действие и перемещение
(сколько чего — задаёт набор правил). Позиций и расстояний нет: достать можно любого
противника, а перемещение тратится на выход из боя. Тактическая карта — отдельный
большой блок, он в бэклоге.

Бой заканчивается сам, когда на ногах остаётся одна сторона. «На ногах» — жив,
не сбежал и хитов больше нуля.
"""

from ai_dnd.rules.actions import (
    Action,
    AttackAction,
    EndCombatAction,
    EndTurnAction,
    FleeAction,
    StartCombatAction,
    UseTurnSlotAction,
)
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.dice import ConstantTerm, DiceExpression, RollMode, parse, roll
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import (
    ActorFled,
    CombatEnded,
    CombatStarted,
    ConditionRemoved,
    DiceRolled,
    Event,
    RoundStarted,
    TurnEnded,
    TurnSlotUsed,
    TurnStarted,
)
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import Actor, CombatState, GameState, TurnSlot

# --- Кто в каком положении ---


def require_combat(state: GameState) -> CombatState:
    if state.combat is None:
        raise ActionError("no_combat")
    return state.combat


def incapacitated_by(ruleset: Ruleset, actor: Actor) -> str | None:
    """Состояние, из-за которого участник не может действовать, или None."""
    for active in actor.conditions:
        definition = ruleset.conditions.get(active.id)
        if definition is not None and definition.prevents_actions:
            return active.id
    return None


def can_fight(state: GameState, actor_id: str) -> bool:
    """Участник на ногах: жив, не сбежал и хитов больше нуля."""
    actor = state.actors.get(actor_id)
    if actor is None or actor.dead:
        return False
    if state.combat is not None and actor_id in state.combat.fled:
        return False
    return actor.hp is None or actor.hp.current > 0


def enemies_of(state: GameState, actor_id: str) -> list[str]:
    """Противники участника в порядке инициативы: живые и не сбежавшие.

    Те, кто лежит с нулём хитов, тоже здесь — по ним можно ударить.
    """
    combat = require_combat(state)
    side = combat.sides.get(actor_id)
    return [
        other
        for other in combat.order
        if combat.sides[other] != side and not state.actors[other].dead and other not in combat.fled
    ]


def standing_sides(state: GameState) -> list[str]:
    """Стороны, у которых ещё есть кто-то на ногах."""
    combat = require_combat(state)
    sides: list[str] = []
    for actor_id in combat.order:
        side = combat.sides[actor_id]
        if side not in sides and can_fight(state, actor_id):
            sides.append(side)
    return sides


def combat_end(state: GameState, visibility: str) -> CombatEnded | None:
    """Событие конца боя, если на ногах осталось меньше двух сторон."""
    if state.combat is None:
        return None
    sides = standing_sides(state)
    if len(sides) >= 2:
        return None
    return CombatEnded(visibility=visibility, winner=sides[0] if sides else None)


def require_turn(state: GameState, actor_id: str, slot: TurnSlot | None = None) -> CombatState:
    """Бой, в котором сейчас ход этого участника и нужная часть хода ещё не потрачена."""
    combat = require_combat(state)
    if combat.current != actor_id:
        raise ActionError("not_your_turn", actor_id=actor_id, current=combat.current)
    if slot is not None and getattr(combat.remaining, slot) <= 0:
        raise ActionError("turn_slot_spent", actor_id=actor_id, slot=slot)
    return combat


# --- Начало боя ---


def start_combat(
    ruleset: Ruleset, state: GameState, action: StartCombatAction, rng: Rng
) -> list[Event]:
    if not ruleset.combat.enabled:
        raise ActionError("combat_disabled")
    if state.combat is not None:
        raise ActionError("combat_already_started")

    sides: dict[str, str] = {}
    for side, actor_ids in action.sides.items():
        for actor_id in actor_ids:
            if actor_id in sides:
                raise ActionError("actor_on_two_sides", actor_id=actor_id)
            if state.actor(actor_id).dead:
                raise ActionError("actor_dead", actor_id=actor_id)
            sides[actor_id] = side
    if len(set(sides.values())) < 2:
        raise ActionError("not_enough_sides")

    dice = parse(ruleset.combat.initiative_dice).terms
    events: list[Event] = []
    rolls: list[tuple[int, int, int, str]] = []
    for position, actor_id in enumerate(sides):
        bonus = state.actor(actor_id).initiative
        terms = (*dice, ConstantTerm.signed(bonus, label="initiative"))
        result = roll(DiceExpression(terms=terms), rng, mode=RollMode.NORMAL)
        events.append(
            DiceRolled(
                visibility=action.visibility, actor_id=actor_id, reason="initiative", roll=result
            )
        )
        # При равенстве ходит тот, у кого выше бонус; затем — кто раньше назван.
        rolls.append((-result.total, -bonus, position, actor_id))

    order = tuple(actor_id for *_, actor_id in sorted(rolls))
    events.append(
        CombatStarted(
            visibility=action.visibility,
            sides=sides,
            order=order,
            initiatives={actor_id: -total for total, _, _, actor_id in rolls},
        )
    )
    events.append(RoundStarted(visibility=action.visibility, round=1))
    events.append(
        TurnStarted(
            visibility=action.visibility, actor_id=order[0], round=1, budget=ruleset.combat.turn
        )
    )
    return events


# --- Ход ---


def end_turn(ruleset: Ruleset, state: GameState, action: EndTurnAction) -> list[Event]:
    combat = require_turn(state, action.actor_id)
    return [
        *_turn_end_events(state, action.actor_id, action.visibility),
        *_next_turn_events(ruleset, state, combat, action.visibility),
    ]


def use_turn_slot(state: GameState, action: UseTurnSlotAction) -> list[Event]:
    require_turn(state, action.actor_id, action.slot)
    return [
        TurnSlotUsed(
            visibility=action.visibility,
            actor_id=action.actor_id,
            slot=action.slot,
            label=action.label,
        )
    ]


def flee(ruleset: Ruleset, state: GameState, action: FleeAction) -> list[Event]:
    actor = state.actor(action.actor_id)
    if actor.dead:
        raise ActionError("actor_dead", actor_id=actor.id)
    condition = incapacitated_by(ruleset, actor)
    if condition is not None:
        raise ActionError("actor_incapacitated", actor_id=actor.id, condition=condition)
    require_turn(state, actor.id, "action")
    require_turn(state, actor.id, "movement")

    events: list[Event] = [
        TurnSlotUsed(visibility=action.visibility, actor_id=actor.id, slot="action"),
        TurnSlotUsed(visibility=action.visibility, actor_id=actor.id, slot="movement"),
        ActorFled(visibility=action.visibility, actor_id=actor.id),
    ]
    after = apply_all(state, events)
    ended = combat_end(after, action.visibility)
    if ended is not None:
        return [*events, ended]
    # Сбежавший больше не ходит: ход переходит дальше.
    return [
        *events,
        *_turn_end_events(after, actor.id, action.visibility),
        *_next_turn_events(ruleset, after, require_combat(after), action.visibility),
    ]


def end_combat(state: GameState, action: EndCombatAction) -> list[Event]:
    require_combat(state)
    return [CombatEnded(visibility=action.visibility, winner=None)]


def _turn_end_events(state: GameState, actor_id: str, visibility: str) -> list[Event]:
    """Конец хода: у состояний участника с длительностью убывает раунд."""
    events: list[Event] = [TurnEnded(visibility=visibility, actor_id=actor_id)]
    events.extend(
        ConditionRemoved(
            visibility=visibility, actor_id=actor_id, condition=item.id, reason="expired"
        )
        for item in state.actor(actor_id).conditions
        if item.rounds is not None and item.rounds <= 1
    )
    return events


def _next_turn_events(
    ruleset: Ruleset, state: GameState, combat: CombatState, visibility: str
) -> list[Event]:
    """Ход следующего по очереди. Погибшие и сбежавшие пропускаются."""
    order = combat.order
    start = order.index(combat.current)
    for step in range(1, len(order) + 1):
        candidate = order[(start + step) % len(order)]
        if state.actors[candidate].dead or candidate in combat.fled:
            continue
        events: list[Event] = []
        round_number = combat.round
        if start + step >= len(order):
            # Очередь прошла по кругу — новый раунд.
            round_number += 1
            events.append(RoundStarted(visibility=visibility, round=round_number))
        events.append(
            TurnStarted(
                visibility=visibility,
                actor_id=candidate,
                round=round_number,
                budget=ruleset.combat.turn,
            )
        )
        return events
    # Ходить некому.
    return [CombatEnded(visibility=visibility, winner=None)]


# --- Что можно сделать ---


def available_actions(ruleset: Ruleset, state: GameState, actor_id: str) -> list[Action]:
    """Действия, которые участник может совершить прямо сейчас.

    Из этого списка выбирают и монстры, и ИИ-игроки, по нему же рисуются кнопки.
    Вне боя и вне своего хода список пуст. Закончить ход можно всегда.
    """
    combat = state.combat
    if combat is None or combat.current != actor_id:
        return []
    actor = state.actor(actor_id)

    actions: list[Action] = []
    able = not actor.dead and incapacitated_by(ruleset, actor) is None
    if able and combat.remaining.action > 0:
        targets = [state.actors[enemy] for enemy in enemies_of(state, actor_id)]
        for attack, profile in actor.attacks.items():
            actions.extend(
                AttackAction(attacker_id=actor_id, target_id=target.id, attack=attack)
                for target in targets
                if target.ac is not None and (profile.damage is None or target.hp is not None)
            )
        if combat.remaining.movement > 0:
            actions.append(FleeAction(actor_id=actor_id))
    actions.append(EndTurnAction(actor_id=actor_id))
    return actions
