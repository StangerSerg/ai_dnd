"""Таблица наборов правил ``rulesets`` (архитектура, 13). Версия неизменяема после публикации."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class RulesetRow(Base):
    __tablename__ = "rulesets"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Автор появится вместе с таблицей users (этап 3); пока nullable без FK.
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    forked_from: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
