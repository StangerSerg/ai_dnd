"""Формулы карточки персонажа: безопасное вычисление и граф зависимостей.

Пакет не импортирует ничего из остального проекта — это проверяет import-linter
(см. pyproject.toml). Им пользуются и шаблоны карточек, и движок правил.
"""

from ai_dnd.sheets.formulas.errors import (
    FormulaError,
    FormulaEvaluationError,
    FormulaGraphError,
    FormulaIssue,
    FormulaSyntaxError,
)
from ai_dnd.sheets.formulas.evaluator import (
    FUNCTIONS,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_LENGTH,
    MAX_FORMULA_NODES,
    Formula,
    Value,
    evaluate,
    parse_formula,
)
from ai_dnd.sheets.formulas.graph import FormulaGraph, find_issues

__all__ = [
    "FUNCTIONS",
    "MAX_FORMULA_DEPTH",
    "MAX_FORMULA_LENGTH",
    "MAX_FORMULA_NODES",
    "Formula",
    "FormulaError",
    "FormulaEvaluationError",
    "FormulaGraph",
    "FormulaGraphError",
    "FormulaIssue",
    "FormulaSyntaxError",
    "Value",
    "evaluate",
    "find_issues",
    "parse_formula",
]
