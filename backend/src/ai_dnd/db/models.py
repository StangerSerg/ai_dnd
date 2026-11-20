"""Все модели SQLAlchemy проекта в одном месте.

Alembic импортирует этот модуль, чтобы при автогенерации миграций видеть все таблицы.
Новый модуль с моделями нужно добавить сюда.
"""

from ai_dnd.admin.models import AuditLogRow, ComplaintRow, SpendLimitRow
from ai_dnd.ai.models import LlmCall
from ai_dnd.billing.models import CreditLedgerRow
from ai_dnd.characters.models import CharacterRow
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.identity.models import AuthSessionRow, OauthAccountRow, UserRow
from ai_dnd.realtime.models import SeatReadRow, SeatRow, SessionKickRow
from ai_dnd.rulesets.models import RulesetRow
from ai_dnd.scenarios.models import (
    ScenarioChunkRow,
    ScenarioEntityRow,
    ScenarioRow,
    ScenarioVersionRow,
)
from ai_dnd.sheets.models import SheetTemplateRow

__all__ = [
    "AuditLogRow",
    "AuthSessionRow",
    "CharacterRow",
    "ComplaintRow",
    "CreditLedgerRow",
    "GameEventRow",
    "GameSessionRow",
    "LlmCall",
    "OauthAccountRow",
    "RulesetRow",
    "ScenarioChunkRow",
    "ScenarioEntityRow",
    "ScenarioRow",
    "ScenarioVersionRow",
    "SeatReadRow",
    "SeatRow",
    "SessionKickRow",
    "SheetTemplateRow",
    "SpendLimitRow",
    "UserRow",
]
