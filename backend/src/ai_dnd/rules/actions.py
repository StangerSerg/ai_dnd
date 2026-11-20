"""Действия — вход движка правил: «что предлагается сделать».

Действие приходит от игрока (кнопка), живого ведущего или ИИ (вызов инструмента).
Движок проверяет его и превращает в события. Видимость действия переходит на все
его события: скрытый бросок ведущего остаётся скрытым.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.rules.dice import DiceNotation, RollMode
from ai_dnd.rules.ruleset import Difficulty
from ai_dnd.rules.state import TurnSlot
from ai_dnd.rules.visibility import PUBLIC, Visibility


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visibility: Visibility = PUBLIC


class CheckAction(_Action):
    """Проверка характеристики или навыка."""

    type: Literal["check"] = "check"
    actor_id: str
    check: str
    # Уровень сложности словом или точное число (например, из сценария). Число важнее.
    difficulty: Difficulty | None = None
    target: int | None = None
    # Преимущество или помеха от обстоятельств — сверх того, что дают состояния.
    mode: RollMode = RollMode.NORMAL


class SaveAction(_Action):
    """Спасбросок."""

    type: Literal["save"] = "save"
    actor_id: str
    save: str
    difficulty: Difficulty | None = None
    target: int | None = None
    mode: RollMode = RollMode.NORMAL


class ContestAction(_Action):
    """Состязание: двое бросают проверки, выигрывает больший итог."""

    type: Literal["contest"] = "contest"
    actor_id: str
    check: str
    opponent_id: str
    opponent_check: str
    mode: RollMode = RollMode.NORMAL
    opponent_mode: RollMode = RollMode.NORMAL


class AttackAction(_Action):
    """Атака против класса доспеха; при попадании — урон."""

    type: Literal["attack"] = "attack"
    attacker_id: str
    target_id: str
    # Id атаки из списка атак нападающего.
    attack: str
    mode: RollMode = RollMode.NORMAL
    # Чем в бою оплачена атака: действием, бонусным действием или ничем («free» —
    # атака вне своего хода, например по решению ведущего). Вне боя не важно.
    slot: Literal["action", "bonus_action", "free"] = "action"


class DamageAction(_Action):
    """Урон без броска атаки: ловушка, падение, заклинание."""

    type: Literal["damage"] = "damage"
    target_id: str
    damage: DiceNotation
    damage_type: str | None = None
    source_id: str | None = None


class HealAction(_Action):
    type: Literal["heal"] = "heal"
    target_id: str
    amount: DiceNotation
    source_id: str | None = None


class ResourceAction(_Action):
    """Потратить или вернуть ресурс: ячейку заклинания, кость хитов."""

    type: Literal["resource"] = "resource"
    actor_id: str
    resource: str
    delta: int


class ApplyConditionAction(_Action):
    type: Literal["apply_condition"] = "apply_condition"
    actor_id: str
    condition: str
    # На сколько раундов. None — пока не снимут.
    rounds: Annotated[int, Field(ge=1)] | None = None


class RemoveConditionAction(_Action):
    type: Literal["remove_condition"] = "remove_condition"
    actor_id: str
    condition: str


class PassTimeAction(_Action):
    """Прошло время: у состояний с длительностью убывают раунды."""

    type: Literal["pass_time"] = "pass_time"
    rounds: Annotated[int, Field(ge=1)] = 1


class RestAction(_Action):
    """Отдых: ресурсы восстанавливаются по правилам набора."""

    type: Literal["rest"] = "rest"
    rest: Literal["short", "long"]
    # Кто отдыхает. Пусто — все живые участники.
    actor_ids: tuple[str, ...] = ()


class StartCombatAction(_Action):
    """Начать бой: бросить инициативу и построить очередь ходов."""

    type: Literal["start_combat"] = "start_combat"
    # Сторона → её участники: {"party": ["mira", "brand"], "enemies": ["goblin_1"]}.
    sides: dict[str, tuple[str, ...]]


class EndTurnAction(_Action):
    type: Literal["end_turn"] = "end_turn"
    actor_id: str


class UseTurnSlotAction(_Action):
    """Потратить часть хода на то, что движок сам не считает: рывок, умение класса,
    взаимодействие с предметом. Что именно произошло, решает ведущий."""

    type: Literal["use_turn_slot"] = "use_turn_slot"
    actor_id: str
    slot: TurnSlot
    label: str | None = None


class FleeAction(_Action):
    """Выйти из боя. Стоит действия и перемещения, ход на этом заканчивается."""

    type: Literal["flee"] = "flee"
    actor_id: str


class HideAction(_Action):
    """Спрятаться (скилл stealth). В бою стоит действия; вне боя — без бюджета хода.

    Даёт состояние ``hidden``: преимущество на свои атаки, помеха на атаки по скрытому.
    Снимается собственной атакой.
    """

    type: Literal["hide"] = "hide"
    actor_id: str
    slot: Literal["action", "bonus_action", "free"] = "action"


class ProtectAction(_Action):
    """Прикрыть союзника (скилл protect) в свой ход вместо атаки.

    До следующего хода защитника атаки по союзнику идут по КД защитника,
    и урон получает защитник.
    """

    type: Literal["protect"] = "protect"
    actor_id: str
    target_id: str
    slot: Literal["action"] = "action"


class EndCombatAction(_Action):
    """Прекратить бой без победителя: переговоры, сдача, решение ведущего."""

    type: Literal["end_combat"] = "end_combat"


Action = Annotated[
    CheckAction
    | SaveAction
    | ContestAction
    | AttackAction
    | DamageAction
    | HealAction
    | ResourceAction
    | ApplyConditionAction
    | RemoveConditionAction
    | PassTimeAction
    | RestAction
    | StartCombatAction
    | EndTurnAction
    | UseTurnSlotAction
    | FleeAction
    | HideAction
    | ProtectAction
    | EndCombatAction,
    Field(discriminator="type"),
]
