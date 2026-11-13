"""Загрузка персонажей и расчёт их карточек."""

from pathlib import Path

from ai_dnd.characters.schemas import Character
from ai_dnd.sheets.compiled import CompiledTemplate, ComputedSheet


class TemplateMismatchError(Exception):
    """Персонаж создан по другому шаблону или по другой его версии."""

    code = "template_mismatch"

    def __init__(self, expected: str, actual: str) -> None:
        self.params = {"expected": expected, "actual": actual}
        super().__init__(f"{self.code}: персонаж создан по {expected}, а шаблон — {actual}")


def load_character(path: Path) -> Character:
    """Прочитать персонажа из JSON-файла."""
    return Character.model_validate_json(path.read_text(encoding="utf-8"))


def build_sheet(character: Character, template: CompiledTemplate) -> ComputedSheet:
    """Проверить значения персонажа по шаблону и посчитать карточку.

    Если значения не подходят к шаблону — ``SheetValuesError`` со списком проблем.
    """
    if character.template != template.ref:
        raise TemplateMismatchError(expected=character.template, actual=template.ref)
    return template.compute(character.values)
