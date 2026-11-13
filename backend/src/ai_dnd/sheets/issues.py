"""Проблемы шаблона карточки и значений персонажа.

Как и везде, наружу уходят код и параметры: текст для человека собирает фронтенд.
``path`` показывает место: ``fields.dex_mod.formula``, ``layout[1].items[0]``,
``bindings.checks.stealth.bonus``, ``values.hp.current``.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SheetIssue:
    code: str
    path: str
    params: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value!r}" for name, value in self.params.items())
        return f"{self.path}: {self.code}" + (f" ({details})" if details else "")


class SheetError(Exception):
    """Базовая ошибка карточек. Все найденные проблемы — в ``issues``."""

    code = "invalid_sheet"

    def __init__(self, issues: Sequence[SheetIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"{self.code}: " + "; ".join(str(issue) for issue in self.issues))


class TemplateError(SheetError):
    """Шаблон карточки составлен неверно."""

    code = "invalid_template"


class SheetValuesError(SheetError):
    """Значения персонажа не подходят к шаблону или по ним не считаются формулы."""

    code = "invalid_values"
