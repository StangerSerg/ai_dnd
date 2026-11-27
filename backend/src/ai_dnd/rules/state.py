"""Состояние игры с точки зрения движка правил.

Состояние неизменяемо: ``apply`` возвращает новое. Участник — это персонаж игрока или
существо под управлением ведущего; движку всё равно, откуда взялись его числа —
из карточки персонажа или из статблока монстра.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_dnd.rules.dice import DiceNotation
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.ruleset import TurnBudget

# Ресурс с этим id — хиты: по нему работают урон, лечение и правило «0 хитов».
HIT_POINTS = "hp"

# Состояние «спрятался»: его эффекты задаёт набор правил, а снимает своя атака.
HIDDEN = "hidden"

# Из чего состоит ход в бою.
TurnSlot = Literal["action", "bonus_action", "movement"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Resource(_Frozen):
    current: Annotated[int, Field(ge=0)]
    max: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _current_within_max(self) -> Self:
        if self.current > self.max:
            raise ValueError(f"current ({self.current}) больше max ({self.max})")
        return self


class ActiveCondition(_Frozen):
    id: str
    # Сколько раундов осталось. None — пока не снимут.
    rounds: Annotated[int, Field(ge=1)] | None = None


class AttackProfile(_Frozen):
    name: str
    bonus: int
    # Запись урона: «1d8+3». None — атака без урона (захват, толчок).
    damage: DiceNotation | None = None
    damage_type: str | None = None
    # Scenario item id consumed by each attempt, including a miss.
    ammo: str | None = None
    # Actor resource id consumed by each attempt, including a miss (numeric ammo).
    ammo_resource: str | None = None
    # The thrown item itself is consumed by each attempt.
    thrown_item: str | None = None


class Tactics(_Frozen):
    """Простые правила, по которым участник сам выбирает действие в бою.

    Так ходят монстры: решает код, а не LLM (архитектура, раздел 6).
    """

    # Кого бить: самого израненного (по доле оставшихся хитов) или случайного.
    target: Literal["most_wounded", "random"] = "most_wounded"
    # Бежать, когда хитов осталось не больше этой доли от максимума. None — стоять до конца.
    flee_below: Annotated[float, Field(gt=0, le=1)] | None = None


class Actor(_Frozen):
    id: str
    name: str
    # Персонаж игрока или существо под управлением ведущего: от этого зависит,
    # что происходит при 0 хитов.
    kind: Literal["character", "creature"] = "creature"
    # Бонусы проверок и спасбросков по id из набора правил.
    checks: dict[str, int] = Field(default_factory=dict)
    saves: dict[str, int] = Field(default_factory=dict)
    ac: int | None = None
    # Бонус к броску инициативы.
    initiative: int = 0
    # Как участник ходит без подсказки игрока. None — правила по умолчанию.
    tactics: Tactics | None = None
    resources: dict[str, Resource] = Field(default_factory=dict)
    attacks: dict[str, AttackProfile] = Field(default_factory=dict)
    # Id скиллов класса из реестра контента: по ним движок выдаёт боевые опции
    # (stealth → HideAction, protect → ProtectAction).
    skills: tuple[str, ...] = ()
    # Кто прикрывает этого участника (ProtectAction): атаки по нему принимает защитник.
    protected_by: str | None = None
    # Типы урона. Кортежи, а не множества: порядок в сохранённом состоянии должен быть
    # одним и тем же.
    resistances: tuple[str, ...] = ()
    vulnerabilities: tuple[str, ...] = ()
    immunities: tuple[str, ...] = ()
    conditions: tuple[ActiveCondition, ...] = ()
    dead: bool = False

    @property
    def hp(self) -> Resource | None:
        return self.resources.get(HIT_POINTS)

    def condition(self, condition_id: str) -> ActiveCondition | None:
        return next((item for item in self.conditions if item.id == condition_id), None)

    def with_resource(self, resource_id: str, current: int) -> "Actor":
        """Участник с новым текущим значением ресурса (в пределах от 0 до максимума)."""
        resource = self.resources[resource_id]
        clamped = max(0, min(resource.max, current))
        updated = {**self.resources, resource_id: Resource(current=clamped, max=resource.max)}
        return self.model_copy(update={"resources": updated})

    def with_conditions(self, conditions: tuple[ActiveCondition, ...]) -> "Actor":
        return self.model_copy(update={"conditions": conditions})

    def with_protector(self, protector_id: str | None) -> "Actor":
        return self.model_copy(update={"protected_by": protector_id})


class CombatState(_Frozen):
    """Бой: кто за кого, очередь ходов, чей сейчас ход и что у него осталось."""

    round: Annotated[int, Field(ge=0)]
    # Участники в порядке инициативы. Погибшие и сбежавшие остаются в списке —
    # их ход просто пропускается.
    order: tuple[str, ...]
    # Участник → сторона. Противники — все, кто на другой стороне.
    sides: dict[str, str]
    initiatives: dict[str, int]
    current: str
    remaining: TurnBudget
    fled: tuple[str, ...] = ()

    def spend(self, slot: TurnSlot) -> "CombatState":
        """Бой, в котором у текущего участника потрачена часть хода."""
        left = max(0, getattr(self.remaining, slot) - 1)
        return self.model_copy(update={"remaining": self.remaining.model_copy(update={slot: left})})


class GameState(_Frozen):
    actors: dict[str, Actor] = Field(default_factory=dict)
    combat: CombatState | None = None

    def actor(self, actor_id: str) -> Actor:
        try:
            return self.actors[actor_id]
        except KeyError:
            raise ActionError("unknown_actor", actor_id=actor_id) from None

    def with_actor(self, actor: Actor) -> "GameState":
        """Состояние, в котором участник добавлен или заменён."""
        return self.model_copy(update={"actors": {**self.actors, actor.id: actor}})
