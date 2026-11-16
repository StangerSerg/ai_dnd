"""Все модели SQLAlchemy проекта в одном месте.

Alembic импортирует этот модуль, чтобы при автогенерации миграций видеть все таблицы.
Новый модуль с моделями нужно добавить сюда.
"""

from ai_dnd.ai.models import LlmCall
from ai_dnd.game.models import GameEventRow, GameSessionRow

__all__ = ["GameEventRow", "GameSessionRow", "LlmCall"]
