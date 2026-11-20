"""Журнал кредитов ``credit_ledger`` (архитектура, 11.1 и 13).

Записи не исправляются, только добавляются; баланс пользователя — sum(delta).
FK на users: таблицу создаёт миграция 0006 (параллельная задача), цепочку собирает Root.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class CreditLedgerRow(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (Index("ix_credit_ledger_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    # Знаковое число кредитов: начисления положительные, списания отрицательные.
    delta: Mapped[int] = mapped_column(BigInteger)
    # daily_free / purchase / reserve / charge / refund.
    reason: Mapped[str] = mapped_column(String(20))
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
