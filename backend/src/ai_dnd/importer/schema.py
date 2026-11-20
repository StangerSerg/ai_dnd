"""Структурированный ответ импорта: черновик сценария и заметки для автора.

Черновик — обычный ``Scenario`` (схема 8.1): structured-вызов шлюза проверяет его
pydantic-схемой, а ссылки между сущностями — ``find_scenario_issues``. Заметки
фиксируют допущения и вопросы модели, чтобы автор знал, что смотреть в первую очередь.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.scenarios.schemas import Scenario


class ImportNote(BaseModel):
    """Заметка модели для автора: что взято с потолка, что неясно, чего не хватает."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["assumption", "question", "missing"]
    # Место в сценарии, например «scenes.crypt.checks.listen».
    path: str = Field(max_length=200)
    text: str = Field(max_length=1000)


class ImportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: Scenario
    notes: tuple[ImportNote, ...] = ()
