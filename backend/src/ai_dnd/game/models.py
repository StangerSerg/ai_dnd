"""Таблицы игровых сессий: ``game_sessions`` и журнал ``game_events`` (архитектура, 13)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_dnd.db.base import Base


class GameSessionRow(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (Index("ix_game_sessions_status_recruiting", "status", "recruiting"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Сценарий (имя файла без языка) и язык игры. Slug сохраняется для совместимости;
    # игра из базы привязана к неизменяемой версии сценария (подэтап 2.1).
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
    # Привязка к версии сценария, правилам и шаблону (nullable для YAML/memory-режима).
    scenario_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scenario_versions.id"), index=True
    )
    ruleset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("rulesets.id"))
    sheet_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sheet_templates.id")
    )
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    # Сводка «что было раньше» для длинной кампании (подэтап 2.4).
    summary: Mapped[str | None] = mapped_column(Text)
    # Владелец комнаты и токен приглашения (подэтап 3.3).
    host_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Открытый набор за стол (задача #32): лидер открывает стол для зала историй;
    # seats_total — ёмкость (null = без ограничения), recruiting — виден в зале.
    recruiting: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    seats_total: Mapped[int | None] = mapped_column(Integer)


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
