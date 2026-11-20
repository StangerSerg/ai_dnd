"""Места за столом и отметки прочитанного (архитектура, 13; подэтап 3.3)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class SeatRow(Base):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("session_id", "actor_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))
    controller: Mapped[str] = mapped_column(String(10), default="human")
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    character_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("characters.id"))
    # Id актёра в движке: у ведущего — «dm», у игрока — «pc_<hex персонажа>».
    actor_id: Mapped[str] = mapped_column(String(64))
    ai_persona: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    muted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Кто замьютил: "leader" — кнопка лидера, "knockout" — нокаут в бою (автомут).
    mute_source: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionKickRow(Base):
    """Кик лидером команды: повторный вход по приглашению запрещён."""

    __tablename__ = "session_kicks"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SeatReadRow(Base):
    __tablename__ = "seat_reads"

    seat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("seats.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class ChatMessageRow(Base):
    """Чат вне игры (3.4): OOC-сообщения участников стола, отдельно от журнала игры."""

    __tablename__ = "session_chat_messages"
    __table_args__ = (Index("ix_session_chat_session_created", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    # Имя автора на момент отправки: место может быть удалено киком, история остаётся.
    author_name: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
