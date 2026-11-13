"""Таблица ``llm_calls``: каждый вызов языковой модели (архитектура, разделы 10 и 13).

На ней строятся учёт расходов, лимиты и аналитика: токены, доля входа из кэша,
стоимость часа игры.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Identity, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Игровая сессия и тот, кто платит. Внешние ключи появятся вместе с этими таблицами.
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)

    task: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))

    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    reasoning_tokens: Mapped[int] = mapped_column(Integer)
    # Доллары. Шесть знаков после запятой: один ход стоит доли цента.
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)

    # ok или error; у неудачного вызова — короткая причина (timeout, auth и так далее).
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(80))
