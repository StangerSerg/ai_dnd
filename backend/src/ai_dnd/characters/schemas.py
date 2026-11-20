"""Персонаж: ссылка на версию шаблона карточки и значения полей."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.sheets.schemas import TemplateRef


class Character(BaseModel):
    """Хранимая часть персонажа.

    Здесь только то, что вводит игрок. Вычисляемые поля и бонусы проверок каждый раз
    считаются заново по шаблону — см. ``ai_dnd.characters.service.build_sheet``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Версия шаблона, по которой создан персонаж: «srd5-basic@1».
    template: TemplateRef
    # Значения полей по id. Чего нет — берётся из значений по умолчанию шаблона.
    values: dict[str, Any] = Field(default_factory=dict)
    # Id класса из content/classes.yaml. Верхний уровень, не values: шаблон не знает
    # такого поля. None — у персонажей, созданных до введения модели классов.
    class_id: str | None = None
