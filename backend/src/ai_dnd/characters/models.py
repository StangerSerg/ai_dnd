"""Таблица персонажей ``characters`` (архитектура, 13): значения карточки по шаблону."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class CharacterRow(Base):
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sheet_templates.id"))
    name: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    portrait: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
