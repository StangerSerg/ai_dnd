"""Таблицы игровых сессий: ``game_sessions`` и журнал ``game_events`` (архитектура, 13)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class GameSessionRow(Base):
    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Сценарий (имя файла без языка) и язык игры. Ссылка на версию сценария в базе
    # появится, когда сценарии переедут туда (подэтап 2.1).
    scenario: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(8))
    dm_mode: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Неизменная часть: уровень импровизации, места за столом.
    setup: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Снимок состояния после последнего события журнала.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Номер последнего события: следующее получит last_seq + 1.
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class GameEventRow(Base):
    __tablename__ = "game_events"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    visibility: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
