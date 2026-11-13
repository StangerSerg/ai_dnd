"""Применение событий к состоянию: ``apply(state, event) -> state``.

Здесь нет ни бросков, ни набора правил — только то, что записано в событии. Поэтому
состояние можно восстановить, проиграв журнал событий с начала.
"""

from collections.abc import Iterable
from typing import assert_never

from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.events import (
    ActorDied,
    ActorFled,
    AttackResolved,
    CheckResolved,
    CombatEnded,
    CombatStarted,
    ConditionApplied,
    ConditionRemoved,
    ContestResolved,
    DamageDealt,
    DiceRolled,
    Event,
    Healed,
    ResourceChanged,
    Rested,
    RoundStarted,
    TimePassed,
    TurnEnded,
    TurnSlotUsed,
    TurnStarted,
)
from ai_dnd.rules.ruleset import TurnBudget
from ai_dnd.rules.state import HIT_POINTS, ActiveCondition, Actor, CombatState, GameState


def apply(state: GameState, event: Event) -> GameState:
    """Состояние после события. Исходное состояние не меняется."""
    match event:
        case DamageDealt():
            return _change_resource(state, event.target_id, HIT_POINTS, -event.amount)
        case Healed():
            return _change_resource(state, event.target_id, HIT_POINTS, event.amount)
        case ResourceChanged():
            return _change_resource(state, event.actor_id, event.resource, event.delta)
        case ActorDied():
            actor = state.actor(event.actor_id)
            return state.with_actor(actor.model_copy(update={"dead": True}))
        case ConditionApplied():
            actor = state.actor(event.actor_id)
            return state.with_actor(_add_condition(actor, event.condition, event.rounds))
        case ConditionRemoved():
            actor = state.actor(event.actor_id)
            kept = tuple(item for item in actor.conditions if item.id != event.condition)
            return state.with_actor(actor.with_conditions(kept))
        case TimePassed():
            return _pass_time(state, event.rounds)
        case CombatStarted():
            # Раунд и текущего участника выставят RoundStarted и TurnStarted сразу следом.
            combat = CombatState(
                round=0,
                order=event.order,
                sides=event.sides,
                initiatives=event.initiatives,
                current=event.order[0],
                remaining=TurnBudget(action=0, bonus_action=0, movement=0),
            )
            return _with_combat(state, combat)
        case RoundStarted():
            combat = _in_combat(state)
            return _with_combat(state, combat.model_copy(update={"round": event.round}))
        case TurnStarted():
            combat = _in_combat(state)
            started = combat.model_copy(
                update={"current": event.actor_id, "round": event.round, "remaining": event.budget}
            )
            return _with_combat(state, started)
        case TurnSlotUsed():
            return _with_combat(state, _in_combat(state).spend(event.slot))
        case TurnEnded():
            return state.with_actor(_age_conditions(state.actor(event.actor_id), 1))
        case ActorFled():
            combat = _in_combat(state)
            fled = (*combat.fled, event.actor_id)
            return _with_combat(state, combat.model_copy(update={"fled": fled}))
        case CombatEnded():
            return _with_combat(state, None)
        case DiceRolled() | CheckResolved() | ContestResolved() | AttackResolved() | Rested():
            # Эти события только сообщают о случившемся, состояние меняют другие.
            return state
        case _:
            assert_never(event)


def apply_all(state: GameState, events: Iterable[Event]) -> GameState:
    for event in events:
        state = apply(state, event)
    return state


def _change_resource(state: GameState, actor_id: str, resource_id: str, delta: int) -> GameState:
    actor = state.actor(actor_id)
    resource = actor.resources.get(resource_id)
    if resource is None:
        raise RulesError("unknown_resource", actor_id=actor_id, resource=resource_id)
    return state.with_actor(actor.with_resource(resource_id, resource.current + delta))


def _add_condition(actor: Actor, condition_id: str, rounds: int | None) -> Actor:
    """Повторное наложение не складывается: остаётся более долгий срок."""
    existing = actor.condition(condition_id)
    if existing is not None:
        if existing.rounds is None or (rounds is not None and rounds <= existing.rounds):
            return actor
        others = tuple(item for item in actor.conditions if item.id != condition_id)
        return actor.with_conditions((*others, ActiveCondition(id=condition_id, rounds=rounds)))
    return actor.with_conditions(
        (*actor.conditions, ActiveCondition(id=condition_id, rounds=rounds))
    )


def _pass_time(state: GameState, rounds: int) -> GameState:
    for actor in state.actors.values():
        aged = _age_conditions(actor, rounds)
        if aged is not actor:
            state = state.with_actor(aged)
    return state


def _age_conditions(actor: Actor, rounds: int) -> Actor:
    """Участник, у которого состояния с длительностью стали на ``rounds`` раундов короче.

    Истёкшие состояния пропадают, бессрочные остаются как есть.
    """
    remaining: list[ActiveCondition] = []
    for item in actor.conditions:
        if item.rounds is None:
            remaining.append(item)
        elif item.rounds > rounds:
            remaining.append(ActiveCondition(id=item.id, rounds=item.rounds - rounds))
    if tuple(remaining) == actor.conditions:
        return actor
    return actor.with_conditions(tuple(remaining))


def _in_combat(state: GameState) -> CombatState:
    if state.combat is None:
        raise RulesError("no_combat")
    return state.combat


def _with_combat(state: GameState, combat: CombatState | None) -> GameState:
    return state.model_copy(update={"combat": combat})
