"""Админка (3.9): журнал аудита и переопределения лимитов расходов."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base

# scope="global" хранится с scope_id = ZERO_UUID (уникальность (scope, scope_id)).
ZERO_UUID = uuid.UUID(int=0)


class AuditLogRow(Base):
    """Каждое админ-действие: кто, что, над чем, детали. Actor nullable = система."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SpendLimitRow(Base):
    """Переопределения лимитов поверх env: user/session — daily_credits,
    global — daily_usd/monthly_usd (предохранители). Null = действует env."""

    __tablename__ = "spend_limits"
    __table_args__ = (UniqueConstraint("scope", "scope_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(10))
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=ZERO_UUID)
    daily_credits: Mapped[int | None] = mapped_column(Integer)
    daily_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    monthly_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ComplaintRow(Base):
    """Жалоба игрока на сессию/событие/пользователя; очередь модератора (3.9)."""

    __tablename__ = "complaints"
    __table_args__ = (Index("ix_complaints_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(500))
