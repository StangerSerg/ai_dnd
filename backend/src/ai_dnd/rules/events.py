"""События — выход движка правил: «что произошло» (архитектура, раздел 2.3).

Событие — свершившийся факт: его записывают в журнал, показывают игрокам и отдают ИИ
для описания. В событии есть всё, чтобы применить его к состоянию без набора правил,
поэтому журнал можно проиграть заново, даже если правила с тех пор изменились.

У каждого события есть видимость. Номер, время и сессию добавляет журнал, не движок.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.rules.dice import RollMode, RollResult
from ai_dnd.rules.ruleset import Difficulty, Outcome, TurnBudget
from ai_dnd.rules.state import TurnSlot
from ai_dnd.rules.visibility import PUBLIC, Visibility

RollReason = Literal["check", "save", "contest", "attack", "damage", "healing", "initiative"]
DamageModifier = Literal["resistance", "vulnerability", "immunity"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visibility: Visibility = PUBLIC


class DiceRolled(_Event):
    """Бросок костей со всеми подробностями: его показывает карточка броска и 3D-кубики."""

    type: Literal["DiceRolled"] = "DiceRolled"
    actor_id: str | None
    reason: RollReason
    roll: RollResult


class CheckResolved(_Event):
    """Итог проверки или спасброска."""

    type: Literal["CheckResolved"] = "CheckResolved"
    actor_id: str
    kind: Literal["check", "save"]
    check: str
    outcome: Outcome
    # Итог броска и «чистое» значение костей. None, если бросать не пришлось.
    total: int | None
    natural: int | None
    # Сложность числом (если способ разрешения сравнивает с числом) и словом.
    target: int | None
    difficulty: Difficulty | None
    mode: RollMode
    # Состояние, из-за которого проверка провалена без броска.
    auto_failed_by: str | None = None


class ContestResolved(_Event):
    type: Literal["ContestResolved"] = "ContestResolved"
    actor_id: str
    check: str
    # Итоги сторон. None — сторона провалила проверку без броска (из-за состояния).
    total: int | None
    opponent_id: str
    opponent_check: str
    opponent_total: int | None
    # None — ничья: положение дел не меняется.
    winner_id: str | None


class AttackResolved(_Event):
    type: Literal["AttackResolved"] = "AttackResolved"
    attacker_id: str
    target_id: str
    attack: str
    attack_name: str
    # success — попадание, critical_success — критическое, failure — промах,
    # critical_failure — промах при любых бонусах.
    outcome: Outcome
    total: int
    natural: int | None
    target_ac: int
    mode: RollMode
    # Цель прикрыта (ProtectAction): бросок шёл по КД этого защитника,
    # и при попадании урон получает он, а не target_id.
    redirected_to_id: str | None = None


class DamageDealt(_Event):
    type: Literal["DamageDealt"] = "DamageDealt"
    target_id: str
    source_id: str | None
    # Урон после сопротивлений и уязвимостей и сколько выпало на костях.
    amount: int
    rolled: int
    damage_type: str | None
    modifiers: tuple[DamageModifier, ...] = ()
    hp_after: int


class Healed(_Event):
    type: Literal["Healed"] = "Healed"
    target_id: str
    source_id: str | None
    # Сколько хитов восстановлено на самом деле (не выше максимума) и сколько выпало.
    amount: int
    rolled: int
    hp_after: int


class ActorDied(_Event):
    type: Literal["ActorDied"] = "ActorDied"
    actor_id: str


class ResourceChanged(_Event):
    type: Literal["ResourceChanged"] = "ResourceChanged"
    actor_id: str
    resource: str
    delta: int
    current: int


class ConditionApplied(_Event):
    type: Literal["ConditionApplied"] = "ConditionApplied"
    actor_id: str
    condition: str
    rounds: int | None


class ConditionRemoved(_Event):
    type: Literal["ConditionRemoved"] = "ConditionRemoved"
    actor_id: str
    condition: str
    reason: Literal["removed", "expired", "healed", "attacked"]


class TimePassed(_Event):
    type: Literal["TimePassed"] = "TimePassed"
    rounds: int


class Rested(_Event):
    type: Literal["Rested"] = "Rested"
    rest: Literal["short", "long"]
    actor_ids: tuple[str, ...]


# --- Бой ---


class CombatStarted(_Event):
    type: Literal["CombatStarted"] = "CombatStarted"
    # Участник → сторона, очередь ходов и итоги бросков инициативы.
    sides: dict[str, str]
    order: tuple[str, ...]
    initiatives: dict[str, int]


class RoundStarted(_Event):
    type: Literal["RoundStarted"] = "RoundStarted"
    round: int


class TurnStarted(_Event):
    type: Literal["TurnStarted"] = "TurnStarted"
    actor_id: str
    round: int
    # Что участник может сделать за этот ход.
    budget: TurnBudget


class TurnSlotUsed(_Event):
    """Потрачена часть хода: действие, бонусное действие или перемещение."""

    type: Literal["TurnSlotUsed"] = "TurnSlotUsed"
    actor_id: str
    slot: TurnSlot
    # На что потрачено, если это не видно из соседних событий.
    label: str | None = None


class TurnEnded(_Event):
    """Ход закончен. У состояний участника с длительностью убывает один раунд."""

    type: Literal["TurnEnded"] = "TurnEnded"
    actor_id: str


class ActorFled(_Event):
    type: Literal["ActorFled"] = "ActorFled"
    actor_id: str


class ProtectionStarted(_Event):
    """Защитник прикрыл союзника: атаки по союзнику принимает защитник."""

    type: Literal["ProtectionStarted"] = "ProtectionStarted"
    protector_id: str
    target_id: str


class ProtectionEnded(_Event):
    """Прикрытие спало: истёк ход защитника или защитник выбыл из строя."""

    type: Literal["ProtectionEnded"] = "ProtectionEnded"
    protector_id: str
    target_id: str
    reason: Literal["expired", "protector_fell"]


class CombatEnded(_Event):
    type: Literal["CombatEnded"] = "CombatEnded"
    # Сторона, которая осталась на ногах. None — бой прекращён без победителя.
    winner: str | None


Event = Annotated[
    DiceRolled
    | CheckResolved
    | ContestResolved
    | AttackResolved
    | DamageDealt
    | Healed
    | ActorDied
    | ResourceChanged
    | ConditionApplied
    | ConditionRemoved
    | TimePassed
    | Rested
    | CombatStarted
    | RoundStarted
    | TurnStarted
    | TurnSlotUsed
    | TurnEnded
    | ActorFled
    | ProtectionStarted
    | ProtectionEnded
    | CombatEnded,
    Field(discriminator="type"),
]
