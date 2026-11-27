"""Схема шаблона карточки персонажа (архитектура, раздел 7.1).

Шаблон состоит из трёх частей:

- ``fields`` — что хранится в карточке;
- ``layout`` — как это показано;
- ``bindings`` — как полями пользуется движок правил.

Здесь описана только структура документа. Связи между частями — ссылки на поля,
формулы, циклы — проверяет ``compile_template`` из ``ai_dnd.sheets.compiled``.
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)

from ai_dnd.i18n import LocalizedText

MAX_FIELDS = 300
MAX_SECTIONS = 50
MAX_OPTIONS = 50
MAX_LIST_COLUMNS = 10
MAX_BINDINGS = 200

# Id поля — это имя переменной в формулах, поэтому только латиница, цифры и «_».
FieldId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)]
TemplateId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]
# Ссылка на версию шаблона: «srd5-basic@3».
TemplateRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*@[1-9][0-9]*$")]
# Ссылка на версию набора правил: «dnd5e-srd@1».
RulesetRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*@[1-9][0-9]*$")]
OptionValue = Annotated[str, StringConstraints(min_length=1, max_length=40)]

type Number = StrictInt | StrictFloat


class _Model(BaseModel):
    # Лишние ключи — ошибка: опечатка в названии не должна молча игнорироваться.
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Поля ---


class _FieldBase(_Model):
    id: FieldId
    label: LocalizedText


class NumberField(_FieldBase):
    type: Literal["number"]
    default: Number = 0
    min: Number | None = None
    max: Number | None = None
    # Дробные значения нужны редко (вес, деньги), поэтому по умолчанию число целое.
    integer: StrictBool = True


class TextField(_FieldBase):
    type: Literal["text"]
    default: str = ""


class LongTextField(_FieldBase):
    type: Literal["longtext"]
    default: str = ""


class SelectOption(_Model):
    value: OptionValue
    label: LocalizedText


class SelectField(_FieldBase):
    type: Literal["select"]
    options: tuple[SelectOption, ...] = Field(min_length=1, max_length=MAX_OPTIONS)
    # Не задано — первый вариант из списка.
    default: OptionValue | None = None

    @property
    def default_value(self) -> str:
        return self.options[0].value if self.default is None else self.default


class CheckboxField(_FieldBase):
    type: Literal["checkbox"]
    default: StrictBool = False


class ResourceField(_FieldBase):
    """Счётчик с текущим значением и максимумом: хиты, ячейки заклинаний.

    В формулах текущее значение доступно по id поля (``hp``), максимум — с суффиксом
    ``_max`` (``hp_max``). Максимум либо считается формулой, либо хранится в персонаже.
    """

    type: Literal["resource"]
    max_formula: str | None = None
    # Максимум для нового персонажа, если формулы нет.
    default_max: Annotated[StrictInt, Field(ge=0)] = 0


class ComputedField(_FieldBase):
    type: Literal["computed"]
    formula: str


class ListColumn(_Model):
    """Колонка списка. Короткая запись — просто тип: ``{"name": "text"}``."""

    type: Literal["text", "longtext", "number", "checkbox"]
    label: LocalizedText | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_short_form(cls, data: object) -> object:
        return {"type": data} if isinstance(data, str) else data


class ListField(_FieldBase):
    """Список однотипных записей: снаряжение, атаки, заклинания."""

    type: Literal["list"]
    item: dict[FieldId, ListColumn] = Field(min_length=1, max_length=MAX_LIST_COLUMNS)


class ImageField(_FieldBase):
    type: Literal["image"]


SheetField = Annotated[
    NumberField
    | TextField
    | LongTextField
    | SelectField
    | CheckboxField
    | ResourceField
    | ComputedField
    | ListField
    | ImageField,
    Field(discriminator="type"),
]


# --- Раскладка ---


class LayoutSection(_Model):
    section: LocalizedText
    cols: int = Field(default=1, ge=1, le=12)
    items: tuple[FieldId, ...] = ()


# --- Привязки ---


class CheckBinding(_Model):
    """Проверка, которую умеет делать персонаж, и формула её бонуса."""

    label: LocalizedText
    bonus: str


class AttacksBinding(_Model):
    """Атаки персонажа: поле-список и его колонки, которые нужны движку."""

    field: FieldId
    # Колонки: название и бонус атаки, запись урона («1d8+3»), id типа урона из набора правил.
    name: FieldId = "name"
    bonus: FieldId = "bonus"
    damage: FieldId = "damage"
    damage_type: FieldId | None = None
    # Колонка с id ресурса актёра — числовой боезапас атаки. None — атака без расхода.
    ammo_resource: FieldId | None = None


class Bindings(_Model):
    """Как движок правил пользуется карточкой.

    Привязка даёт число, а не бросок: какие кости бросать и как читать результат,
    решает набор правил (архитектура, раздел 6.1). Поэтому одна и та же карточка
    годится и для «d20 + бонус», и для «2d6 + бонус».
    """

    checks: dict[FieldId, CheckBinding] = Field(default_factory=dict, max_length=MAX_BINDINGS)
    saves: dict[FieldId, CheckBinding] = Field(default_factory=dict, max_length=MAX_BINDINGS)
    # Id поля-ресурса с хитами.
    hp: FieldId | None = None
    # Формулы класса доспеха и бонуса инициативы.
    ac: str | None = None
    initiative: str | None = None
    attacks: AttacksBinding | None = None


# --- Шаблон ---


class SheetTemplate(_Model):
    id: TemplateId
    version: Annotated[StrictInt, Field(ge=1)]
    name: LocalizedText
    # Версия набора правил, для которой сделан шаблон: «dnd5e-srd@1».
    ruleset: RulesetRef | None = None
    fields: tuple[SheetField, ...] = Field(max_length=MAX_FIELDS)
    layout: tuple[LayoutSection, ...] = Field(default=(), max_length=MAX_SECTIONS)
    bindings: Bindings = Field(default_factory=Bindings)

    @property
    def ref(self) -> str:
        """Ссылка на эту версию шаблона: ``srd5-basic@3``."""
        return f"{self.id}@{self.version}"
