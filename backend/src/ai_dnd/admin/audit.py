"""Журнал админ-действий: запись в той же транзакции, что и мутация (атомарно)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai_dnd.admin.models import AuditLogRow


async def record(
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Добавляет строку аудита (flush без commit) и возвращает её id.

    Вызывающий коммитит вместе с самой мутацией; id используется, например,
    как ref_id записи credit_ledger при ручной корректировке баланса.
    """
    row = AuditLogRow(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details if details is not None else {},
    )
    session.add(row)
    await session.flush()
    return row.id
