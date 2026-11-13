"""Тактика: как участник боя сам выбирает действие (архитектура, раздел 6).

За монстров решает код, а не LLM — простые правила из ``Tactics``: кого бить и когда
бежать. LLM потом только описывает раунд. Те же правила годятся, чтобы прогнать бой
целиком без игроков: в тестах и в песочнице редактора правил.
"""

from ai_dnd.rules.actions import Action, AttackAction, EndTurnAction, FleeAction
from ai_dnd.rules.combat import can_fight
from ai_dnd.rules.dice import parse
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.events import Event
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.state import Actor, GameState, Tactics

MAX_ROUNDS = 100


def choose_action(engine: RulesEngine, state: GameState, actor_id: str, rng: Rng) -> Action:
    """Действие участника по его тактике — всегда одно из допустимых.

    Порядок простой: бежать, если хитов осталось мало; иначе ударить выбранную цель
    самой сильной атакой; если ударить нечем или некого — закончить ход.
    """
    options = engine.available_actions(state, actor_id)
    if not options:
        raise RulesError("not_your_turn", actor_id=actor_id)
    actor = state.actor(actor_id)
    tactics = actor.tactics or Tactics()

    escape = next((option for option in options if isinstance(option, FleeAction)), None)
    if (
        escape is not None
        and tactics.flee_below is not None
        and _hp_fraction(actor) <= tactics.flee_below
    ):
        return escape

    attacks = [option for option in options if isinstance(option, AttackAction)]
    if not attacks:
        return EndTurnAction(actor_id=actor_id)

    target_id = _choose_target(state, attacks, tactics, rng)
    candidates = [attack for attack in attacks if attack.target_id == target_id]
    # При равном среднем уроне — атака, записанная раньше.
    return max(candidates, key=lambda attack: _average_damage(actor, attack.attack))


def run_combat(
    engine: RulesEngine, state: GameState, rng: Rng, *, max_rounds: int = MAX_ROUNDS
) -> tuple[list[Event], GameState]:
    """Довести начатый бой до конца: каждый участник ходит по своей тактике.

    Возвращает все события и состояние после боя. Если бой не закончился за
    ``max_rounds`` раундов, это ошибка — иначе при неудачных правилах цикл был бы вечным.
    """
    events: list[Event] = []
    while state.combat is not None:
        if state.combat.round > max_rounds:
            raise RulesError("combat_too_long", rounds=max_rounds)
        action = choose_action(engine, state, state.combat.current, rng)
        happened, state = engine.run(state, action, rng)
        events.extend(happened)
    return events, state


def _choose_target(
    state: GameState, attacks: list[AttackAction], tactics: Tactics, rng: Rng
) -> str:
    targets = list(dict.fromkeys(attack.target_id for attack in attacks))
    # Сначала те, кто ещё на ногах: добивать лежащих незачем, пока есть кому ответить.
    standing = [target for target in targets if can_fight(state, target)] or targets
    if tactics.target == "random":
        return standing[rng.randint(0, len(standing) - 1)]
    # Самый израненный; при равенстве — тот, кто раньше в очереди инициативы.
    return min(standing, key=lambda target: _hp_fraction(state.actors[target]))


def _hp_fraction(actor: Actor) -> float:
    """Доля оставшихся хитов: 1.0 — цел, 0.0 — на нуле."""
    hp = actor.hp
    if hp is None:
        return 1.0
    return hp.current / hp.max if hp.max > 0 else 0.0


def _average_damage(actor: Actor, attack_id: str) -> float:
    damage = actor.attacks[attack_id].damage
    if damage is None:
        return 0.0
    low, high = parse(damage).bounds()
    return (low + high) / 2
