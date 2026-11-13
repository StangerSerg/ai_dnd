"""Набор правил как данные (архитектура, раздел 6.1).

Набор правил — документ, который настраивает готовые «кирпичики» движка: какие кости
бросать на проверку и как читать результат, что значат уровни сложности, какие есть
проверки, ресурсы и состояния, как устроен бой. Своего кода автор не пишет.

Встроенный набор ``dnd5e-srd`` описан тем же способом, что и пользовательские.
"""

import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from ai_dnd.rules.dice import DiceNotation, RollMode
from ai_dnd.rules.errors import RulesError

# Id — латиница, цифры и «_»: на них ссылаются карточки, сценарии и инструменты ИИ.
EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)]
RulesetId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]
# Ссылка на версию набора: «dnd5e-srd@1».
RulesetRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*@[1-9][0-9]*$")]

# Подпись: строка или словарь по языкам. Формат тот же, что у ai_dnd.i18n.LocalizedText,
# но описан отдельно: движок правил не импортирует остальной проект.
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
_Language = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
Label = _Text | Annotated[dict[_Language, _Text], Field(min_length=1)]
LongLabel = _LongText | Annotated[dict[_Language, _LongText], Field(min_length=1)]


class Difficulty(StrEnum):
    """Уровни сложности. ИИ называет уровень словом, число берётся из набора правил."""

    VERY_EASY = "very_easy"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"
    NEARLY_IMPOSSIBLE = "nearly_impossible"


class Outcome(StrEnum):
    """Исходы проверки. Набор один на все системы — его понимают ИИ и интерфейс."""

    CRITICAL_FAILURE = "critical_failure"
    FAILURE = "failure"
    PARTIAL_SUCCESS = "partial_success"
    SUCCESS = "success"
    CRITICAL_SUCCESS = "critical_success"

    @property
    def succeeded(self) -> bool:
        """Удалось ли задуманное хотя бы частично."""
        return self in (Outcome.PARTIAL_SUCCESS, Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Способ разрешения проверок ---


class RollVsTarget(_Model):
    """«Кости + бонус против сложности»: успех, если итог не меньше сложности (5e)."""

    type: Literal["roll_vs_target"]
    dice: DiceNotation = "1d20"
    # Критические исходы по «чистому» значению костей. None — критических исходов нет.
    critical_success: StrictInt | None = None
    critical_failure: StrictInt | None = None


class Band(_Model):
    """Диапазон итогов: от ``min`` и выше — такой-то исход."""

    min: StrictInt
    outcome: Outcome


class RollBands(_Model):
    """«Кости + бонус, исход по диапазонам»: например, 2d6 — до 6 провал, 7–9 частичный
    успех, от 10 успех. Сложность здесь не цель, а поправка к броску."""

    type: Literal["roll_bands"]
    dice: DiceNotation = "2d6"
    bands: tuple[Band, ...] = Field(min_length=1)
    # Исход, если итог ниже всех диапазонов.
    below: Outcome = Outcome.FAILURE


Resolution = Annotated[RollVsTarget | RollBands, Field(discriminator="type")]


# --- Проверки, ресурсы, состояния ---


class CheckDefinition(_Model):
    label: Label
    # Проверка навыка — это проверка характеристики: если у участника нет своего бонуса
    # для навыка, берётся бонус этой проверки.
    ability: EntityId | None = None


class Recovery(_Model):
    """Когда и насколько восстанавливается ресурс."""

    rest: Literal["short", "long"]
    amount: Literal["full", "half"] = "full"


class ResourceDefinition(_Model):
    label: Label
    recovery: Recovery | None = None


class EffectTarget(StrEnum):
    """На какие броски влияет состояние."""

    CHECKS = "checks"
    SAVES = "saves"
    ATTACKS = "attacks"
    # Атаки по тому, кто находится в этом состоянии.
    ATTACKS_AGAINST = "attacks_against"


class Effect(_Model):
    """Влияние состояния на броски: преимущество или помеха, поправка, провал без броска."""

    applies_to: EffectTarget
    # Только эти проверки или спасброски; пусто — все.
    only: tuple[EntityId, ...] = ()
    mode: Literal[RollMode.ADVANTAGE, RollMode.DISADVANTAGE] | None = None
    bonus: StrictInt = 0
    auto_fail: bool = False


class ConditionDefinition(_Model):
    label: Label
    effects: tuple[Effect, ...] = ()
    # В этом состоянии участник не может действовать (без сознания, парализован).
    prevents_actions: bool = False


# --- Бой ---


class ZeroHitPoints(_Model):
    """Что происходит, когда хиты упали до нуля."""

    # Состояние, которое получает персонаж игрока.
    condition: EntityId | None = None
    # Существа под управлением ведущего погибают сразу.
    creatures_die: bool = True


class TurnBudget(_Model):
    """Что участник может сделать за один свой ход."""

    action: Annotated[StrictInt, Field(ge=0)] = 1
    bonus_action: Annotated[StrictInt, Field(ge=0)] = 1
    movement: Annotated[StrictInt, Field(ge=0)] = 1


class CombatSettings(_Model):
    enabled: bool = True
    # Инициатива: эти кости плюс бонус инициативы участника, ходят от большего к меньшему.
    initiative_dice: DiceNotation = "1d20"
    turn: TurnBudget = Field(default_factory=TurnBudget)
    attack_dice: DiceNotation = "1d20"
    # «Чистое» значение костей атаки: от этого и выше — критическое попадание,
    # от этого и ниже — промах при любых бонусах. None — таких правил нет.
    critical_hit: StrictInt | None = None
    automatic_miss: StrictInt | None = None
    critical_damage: Literal["double_dice", "none"] = "double_dice"
    zero_hp: ZeroHitPoints = Field(default_factory=ZeroHitPoints)


# --- Набор правил ---


class Ruleset(_Model):
    id: RulesetId
    version: Annotated[StrictInt, Field(ge=1)]
    name: Label
    resolution: Resolution
    # Число для каждого уровня сложности. Для «против сложности» это сама сложность,
    # для «диапазонов» — поправка к броску.
    difficulty: dict[Difficulty, StrictInt]
    checks: dict[EntityId, CheckDefinition]
    saves: dict[EntityId, CheckDefinition] = Field(default_factory=dict)
    damage_types: dict[EntityId, Label] = Field(default_factory=dict)
    resources: dict[EntityId, ResourceDefinition] = Field(default_factory=dict)
    conditions: dict[EntityId, ConditionDefinition] = Field(default_factory=dict)
    combat: CombatSettings = Field(default_factory=CombatSettings)
    # Краткое описание правил для ИИ (слой 2 промпта).
    summary: LongLabel | None = None

    @property
    def ref(self) -> str:
        """Ссылка на эту версию набора: ``dnd5e-srd@1``."""
        return f"{self.id}@{self.version}"


# --- Проверка связей внутри набора ---


@dataclass(frozen=True, slots=True)
class RulesetIssue:
    """Проблема набора правил: код, место (``conditions.poisoned.effects[0].only``), параметры."""

    code: str
    path: str
    params: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value!r}" for name, value in self.params.items())
        return f"{self.path}: {self.code}" + (f" ({details})" if details else "")


class RulesetError(RulesError):
    """Набор правил составлен неверно. Все найденные проблемы — в ``issues``."""

    def __init__(self, issues: Sequence[RulesetIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("invalid_ruleset", issues=[str(issue) for issue in self.issues])


def find_ruleset_issues(ruleset: Ruleset) -> list[RulesetIssue]:
    """Все проблемы набора правил. Пустой список — набором можно пользоваться."""
    return [
        *_difficulty_issues(ruleset),
        *_resolution_issues(ruleset),
        *_check_issues(ruleset),
        *_condition_issues(ruleset),
        *_combat_issues(ruleset),
    ]


def _difficulty_issues(ruleset: Ruleset) -> Iterator[RulesetIssue]:
    for level in Difficulty:
        if level not in ruleset.difficulty:
            yield RulesetIssue("missing_difficulty", f"difficulty.{level.value}")


def _resolution_issues(ruleset: Ruleset) -> Iterator[RulesetIssue]:
    resolution = ruleset.resolution
    if isinstance(resolution, RollBands):
        thresholds = [band.min for band in resolution.bands]
        for value in sorted({value for value in thresholds if thresholds.count(value) > 1}):
            yield RulesetIssue("duplicate_band", "resolution.bands", {"min": value})


def _check_issues(ruleset: Ruleset) -> Iterator[RulesetIssue]:
    for check_id, check in ruleset.checks.items():
        if check.ability is None:
            continue
        path = f"checks.{check_id}.ability"
        base = ruleset.checks.get(check.ability)
        if base is None:
            yield RulesetIssue("unknown_check", path, {"check": check.ability})
        elif base.ability is not None or check.ability == check_id:
            # Цепочки «навык → навык → характеристика» не нужны и легко зацикливаются.
            yield RulesetIssue("not_a_base_check", path, {"check": check.ability})
    for save_id, save in ruleset.saves.items():
        if save.ability is not None and save.ability not in ruleset.checks:
            yield RulesetIssue("unknown_check", f"saves.{save_id}.ability", {"check": save.ability})


def _condition_issues(ruleset: Ruleset) -> Iterator[RulesetIssue]:
    for condition_id, condition in ruleset.conditions.items():
        for index, effect in enumerate(condition.effects):
            path = f"conditions.{condition_id}.effects[{index}]"
            if effect.mode is None and effect.bonus == 0 and not effect.auto_fail:
                yield RulesetIssue("empty_effect", path)

            is_attack = effect.applies_to in (EffectTarget.ATTACKS, EffectTarget.ATTACKS_AGAINST)
            if is_attack and effect.auto_fail:
                yield RulesetIssue("auto_fail_on_attack", path)
            if is_attack and effect.only:
                yield RulesetIssue("only_on_attack", f"{path}.only")
                continue
            known = ruleset.saves if effect.applies_to is EffectTarget.SAVES else ruleset.checks
            code = "unknown_save" if effect.applies_to is EffectTarget.SAVES else "unknown_check"
            for subject in effect.only:
                if subject not in known:
                    yield RulesetIssue(code, f"{path}.only", {"id": subject})


def _combat_issues(ruleset: Ruleset) -> Iterator[RulesetIssue]:
    condition = ruleset.combat.zero_hp.condition
    if condition is not None and condition not in ruleset.conditions:
        yield RulesetIssue(
            "unknown_condition", "combat.zero_hp.condition", {"condition": condition}
        )
