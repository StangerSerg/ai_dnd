"""Ошибки формул.

Наружу уходят код и параметры, а не готовый текст: сообщение для автора шаблона
собирает и переводит фронтенд.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class FormulaError(Exception):
    """Базовая ошибка формул."""

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        details = ", ".join(f"{name}={value!r}" for name, value in params.items())
        super().__init__(f"{code}: {details}" if details else code)


class FormulaSyntaxError(FormulaError):
    """Формулу не удалось разобрать, или в ней есть запрещённая конструкция."""


class FormulaEvaluationError(FormulaError):
    """Формула разобрана, но вычислить её с этими значениями нельзя."""


@dataclass(frozen=True, slots=True)
class FormulaIssue:
    """Одна проблема в наборе формул: что не так и в каком вычисляемом поле."""

    code: str
    field: str
    # dataclasses.field пишем полностью: имя field занято полем выше.
    params: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:
        details = ", ".join(f"{name}={value!r}" for name, value in self.params.items())
        return f"{self.field}: {self.code}" + (f" ({details})" if details else "")


class FormulaGraphError(FormulaError):
    """Набор формул нельзя вычислить. Все найденные проблемы — в ``issues``."""

    def __init__(self, issues: Sequence[FormulaIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("invalid_formulas", issues=[str(issue) for issue in self.issues])
