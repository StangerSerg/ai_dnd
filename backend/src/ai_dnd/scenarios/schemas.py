"""Схема сценария и статблоков (архитектура, раздел 8.1).

Сценарий — набор сущностей, а не один большой текст: сцены, NPC, предметы, улики,
раздаточные материалы, столкновения, сведения о мире. В промпт ИИ попадает нужная
сцена, а не весь модуль.

У текстов есть видимость: ``read_aloud`` видят игроки, ``dm_notes`` и ``secrets`` —
только ведущий, у улик и раздаточных материалов она задаётся явно и меняется,
когда их открывают игрокам.

Здесь только структура документа. Ссылки между сущностями проверяет
``ai_dnd.scenarios.service.find_scenario_issues``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ai_dnd.i18n import LanguageCode, LocalizedText
from ai_dnd.rules.dice import DiceNotation
from ai_dnd.rules.ruleset import Difficulty, Effect, RulesetRef
from ai_dnd.rules.state import Tactics
from ai_dnd.sheets.schemas import TemplateRef

EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)]
# Статблок из библиотеки («srd:goblin») или свой, описанный в сценарии («cult_leader»).
StatblockRef = Annotated[
    str, StringConstraints(pattern=r"^([a-z][a-z0-9_]*:)?[a-z][a-z0-9_]*$", max_length=80)
]
ContentVisibility = Literal["public", "dm"]

_Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
# Длинный текст на нескольких языках — для библиотек, общих для всех языков.
LocalizedLongText = _Text | Annotated[dict[LanguageCode, _Text], Field(min_length=1)]


class _Model(BaseModel):
    # Лишние ключи — ошибка: опечатка в названии не должна молча игнорироваться.
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Статблоки ---


class StatblockAttack(_Model):
    name: LocalizedText
    bonus: int
    damage: DiceNotation | None = None
    damage_type: EntityId | None = None


class Statblock(_Model):
    """Существо под управлением ведущего: готовые числа для движка правил."""

    name: LocalizedText
    ac: int
    hp: Annotated[int, Field(ge=1)]
    # Кости хитов — для справки и чтобы при желании бросить хиты, а не брать среднее.
    hit_dice: DiceNotation | None = None
    initiative: int = 0
    # Бонусы проверок и спасбросков по id из набора правил. Навык, которого здесь нет,
    # считается по своей характеристике.
    checks: dict[EntityId, int]
    saves: dict[EntityId, int] = Field(default_factory=dict)
    attacks: dict[EntityId, StatblockAttack] = Field(default_factory=dict)
    resistances: tuple[EntityId, ...] = ()
    vulnerabilities: tuple[EntityId, ...] = ()
    immunities: tuple[EntityId, ...] = ()
    tactics: Tactics | None = None
    # Особенности, которые движок не считает сам: их учитывает ведущий.
    traits: LocalizedLongText | None = None


class Bestiary(_Model):
    """Библиотека статблоков. На её монстров ссылаются как ``<id библиотеки>:<id монстра>``."""

    id: EntityId
    name: LocalizedText
    ruleset: RulesetRef
    monsters: dict[EntityId, Statblock]


# --- Сущности сценария ---


class ScenarioInfo(_Model):
    title: _Line
    language: LanguageCode
    ruleset: RulesetRef
    sheet_template: TemplateRef
    start_scene: EntityId
    # Коротко для ведущего: о чём сценарий и чем он должен кончиться.
    synopsis: _Text
    # Тон и манера повествования.
    tone: _Text | None = None
    # Новые YAML обязаны указывать пакет; None допускается только для уже
    # опубликованных legacy-версий, которые разбирает адаптер store.
    class_pack: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$", max_length=40)
    # Поле старой закреплённой версии. Новые YAML с ним не публикуются.
    available_classes: tuple[EntityId, ...] = ()
    # Тумблер предметной механики (подэтап 7). False — предметы живут только в памяти
    # ведущего: контекст без инвентаря, предметные инструменты запрещены, лут не выдаётся.
    items_enabled: bool = True
    # Нативные ярлыки тяжести урона поверх дефолтных (только рендер, не механика).
    # Ключи — канонические английские («light wound», …); допустимость проверяет
    # find_scenario_issues (код unknown_wound_epithet).
    wound_epithets: dict[str, _Line] = Field(default_factory=dict)


class CheckResult(_Model):
    """Что происходит по итогу проверки: какие улики и материалы открываются."""

    reveal: tuple[EntityId, ...] = ()
    # Подсказка ведущему, как описать исход.
    text: _Text | None = None


class SceneCheck(_Model):
    """Проверка, заготовленная автором: навык или спасбросок и точная сложность."""

    id: EntityId
    # Когда проверка уместна — для ведущего.
    description: _Text | None = None
    skill: EntityId | None = None
    save: EntityId | None = None
    dc: int | None = None
    difficulty: Difficulty | None = None
    on_success: CheckResult | None = None
    on_failure: CheckResult | None = None


class SceneRest(_Model):
    """One authored, single-use recovery opportunity in a scene."""

    id: EntityId
    name: _Line
    after_encounters: tuple[EntityId, ...] = Field(min_length=1)
    resources: tuple[Literal["hp", "spell_slots_1"], ...] = Field(min_length=1)


class Scene(_Model):
    id: EntityId
    title: _Line
    # Текст для игроков — его можно зачитать вслух.
    read_aloud: _Text | None = None
    # Только для ведущего.
    dm_notes: _Text | None = None
    npcs: tuple[EntityId, ...] = ()
    items: tuple[EntityId, ...] = ()
    clues: tuple[EntityId, ...] = ()
    handouts: tuple[EntityId, ...] = ()
    encounters: tuple[EntityId, ...] = ()
    # В какие сцены отсюда можно перейти.
    exits: tuple[EntityId, ...] = ()
    checks: tuple[SceneCheck, ...] = ()
    rest: SceneRest | None = None


class Npc(_Model):
    id: EntityId
    name: _Line
    # Внешность и первое впечатление — это видят игроки.
    description: _Text | None = None
    # Характер, цели, манера речи — для ведущего.
    personality: _Text | None = None
    # Только для ведущего.
    secrets: tuple[_Text, ...] = ()
    attitude: Literal["friendly", "neutral", "hostile"] = "neutral"
    # Стартовая сцена NPC: где он живёт, пока ведущий не переместил его (move_npc).
    # Без home присутствие определяется только списком npcs сцены (старое поведение).
    home: EntityId | None = None
    statblock: StatblockRef | None = None


# Предел степени заточки; runtime-заточка (подэтап 4) использует ту же константу.
MAX_ITEM_ENHANCEMENT = 3

ItemKind = Literal["weapon", "armor", "wearable", "consumable", "other"]
ItemSlot = Literal["weapon", "armor", "amulet", "ring", "cloak", "other"]
ItemConsumeTrigger = Literal["use", "attack", "throw", "spend"]


class ItemAttack(_Model):
    """Attack made available by an equipped scenario weapon."""

    name: _Line
    bonus: int
    damage: DiceNotation | None = None
    damage_type: EntityId | None = None


class ItemHeal(_Model):
    """Лечение при использовании: формула как в HealAction.amount («2d4+2»)."""

    type: Literal["heal"]
    amount: DiceNotation


class ItemBuff(_Model):
    """Временные модификаторы. Длительность в раундах; None — пока не снимет ведущий."""

    type: Literal["buff"]
    effects: tuple[Effect, ...] = Field(min_length=1)
    rounds: Annotated[int, Field(ge=1)] | None = None


class ItemSpell(_Model):
    """Свиток. Реестра заклинаний в проекте нет, поэтому ссылка нарративная:
    название заклинания текстом, каст разбирает ведущий (подэтап 4)."""

    type: Literal["spell"]
    spell: _Line


ItemUseEffect = Annotated[ItemHeal | ItemBuff | ItemSpell, Field(discriminator="type")]


class Item(_Model):
    """Предмет сценария. Механика опциональна: без неё предмет остаётся нарративным."""

    id: EntityId
    name: _Line
    description: _Text | None = None
    visibility: ContentVisibility = "public"
    kind: ItemKind | None = None
    slot: ItemSlot | None = None
    quantity: Annotated[int, Field(ge=1)] = 1
    # Движок применяет эти эффекты как есть (решение 18.11): проверки, спасброски, атаки.
    modifiers: tuple[Effect, ...] = ()
    # AC и плоский урон — не броски, Effect их не выражает: отдельные скаляры.
    ac_bonus: int = 0
    damage_bonus: int = 0
    # Стихийный урон оружия: id типа урона из набора правил.
    damage_type: EntityId | None = None
    # Заточка +N: бонус к атаке и урону равен степени. Лимит проверяет валидатор,
    # чтобы превышение попало в issues-отчёт, а не в ошибку схемы.
    enhancement: Annotated[int, Field(ge=0)] = 0
    consumable_effect: ItemUseEffect | None = None
    consume_on: ItemConsumeTrigger | None = None
    # The weapon uses one unit of this scenario item for each attempted attack.
    ammo: EntityId | None = None
    attack: ItemAttack | None = None
    # A spend action can repair another actor, up to this many hit points per unit.
    repair_hp: Annotated[int, Field(ge=1)] | None = None
    # Иконка из фиксированного каталога scenarios/icons.py; None — без иконки.
    # Допустимость id проверяет find_scenario_issues (код unknown_icon).
    icon: EntityId | None = None


class Clue(_Model):
    id: EntityId
    text: _Text
    # Улика скрыта, пока её не открыли игрокам.
    visibility: ContentVisibility = "dm"


class Handout(_Model):
    """Раздаточный материал: письмо, объявление, страница дневника."""

    id: EntityId
    title: _Line
    text: _Text
    visibility: ContentVisibility = "dm"


class Lore(_Model):
    """Сведения о мире, которые не привязаны к одной сцене."""

    id: EntityId
    title: _Line
    text: _Text
    visibility: ContentVisibility = "public"


class EncounterGroup(_Model):
    statblock: StatblockRef
    count: Annotated[int, Field(ge=1, le=20)] = 1
    # Имя, если оно отличается от названия в статблоке («Жрец Змея»).
    name: _Line | None = None


class LootEntry(_Model):
    """Запись таблицы дропа энкаунтера: сценарный предмет, количество и шанс."""

    item_id: EntityId
    quantity: Annotated[int, Field(ge=1)] = 1
    chance: Annotated[float, Field(gt=0, le=1)] = 1.0


class Encounter(_Model):
    """Столкновение: кто противостоит героям."""

    id: EntityId
    title: _Line
    # Обстоятельства и поведение противников — для ведущего.
    description: _Text | None = None
    creatures: tuple[EncounterGroup, ...] = Field(min_length=1)
    # Дроп при победе партии: выдаёт механика, ведущий только нарратирует находку.
    loot: tuple[LootEntry, ...] = ()
    # Факты победы: фиксирует механика тем же коммитом, что CombatEnded; ведущий только нарратирует.
    victory_facts: tuple[_Line, ...] = ()


class MapNode(_Model):
    x: float
    y: float
    icon: _Line | None = None


class ScenarioMap(_Model):
    """Карта мира: сцены — точки, переходы между ними берутся из ``exits``."""

    background: _Line | None = None
    style: _Line = "parchment"
    nodes: dict[EntityId, MapNode] = Field(default_factory=dict)


class Scenario(_Model):
    scenario: ScenarioInfo
    scenes: tuple[Scene, ...] = Field(min_length=1)
    npcs: tuple[Npc, ...] = ()
    items: tuple[Item, ...] = ()
    clues: tuple[Clue, ...] = ()
    handouts: tuple[Handout, ...] = ()
    encounters: tuple[Encounter, ...] = ()
    lore: tuple[Lore, ...] = ()
    # Свои статблоки сценария — в дополнение к библиотечным.
    statblocks: dict[EntityId, Statblock] = Field(default_factory=dict)
    map: ScenarioMap | None = None
