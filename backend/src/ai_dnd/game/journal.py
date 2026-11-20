"""Журнал сессии: события только дописываются (архитектура, раздел 2.3).

Вместе с событиями сохраняется снимок состояния — в той же транзакции, поэтому журнал
и снимок не расходятся. В консоли без базы журнал живёт в памяти.
"""

import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.game.events import JOURNAL_EVENT, JournalEvent
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import SessionSetup, SessionState


class Journal(Protocol):
    @property
    def session_id(self) -> uuid.UUID | None: ...

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        """Дописать события и снимок состояния после них."""
        ...


class MemoryJournal:
    def __init__(self) -> None:
        self.events: list[JournalEvent] = []
        self.state: SessionState | None = None

    @property
    def session_id(self) -> uuid.UUID | None:
        return None

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        self.events.extend(events)
        self.state = state


class DbJournal:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], session_id: uuid.UUID) -> None:
        self._sessions = sessions
        self._session_id = session_id

    @property
    def session_id(self) -> uuid.UUID:
        return self._session_id

    @classmethod
    async def create(
        cls,
        sessions: async_sessionmaker[AsyncSession],
        setup: SessionSetup,
        state: SessionState,
        *,
        scenario_version_id: uuid.UUID | None = None,
        ruleset_id: uuid.UUID | None = None,
        sheet_template_id: uuid.UUID | None = None,
    ) -> "DbJournal":
        """Новая сессия в ``game_sessions`` с начальным снимком состояния.

        Ссылки на версию сценария, правила и шаблон пиннят игру к неизменяемой версии
        (подэтап 2.1); в YAML/memory-режиме остаются None.
        """
        row = GameSessionRow(
            id=uuid.uuid4(),
            scenario=setup.scenario,
            language=setup.language,
            setup=setup.model_dump(mode="json"),
            state=state.model_dump(mode="json"),
            scenario_version_id=scenario_version_id,
            ruleset_id=ruleset_id,
            sheet_template_id=sheet_template_id,
        )
        async with sessions() as session, session.begin():
            session.add(row)
        return cls(sessions, row.id)

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        if not events:
            return
        async with self._sessions() as session, session.begin():
            # Строка сессии блокируется: номера событий не пересекутся, даже если
            # пишут два процесса.
            row = await session.scalar(
                select(GameSessionRow)
                .where(GameSessionRow.id == self._session_id)
                .with_for_update()
            )
            if row is None:
                raise LookupError(f"Нет игровой сессии {self._session_id}")
            seq = row.last_seq
            for event in events:
                seq += 1
                session.add(
                    GameEventRow(
                        session_id=self._session_id,
                        seq=seq,
                        type=event.type,
                        visibility=event.visibility,
                        payload=JOURNAL_EVENT.dump_python(event, mode="json"),
                    )
                )
            row.last_seq = seq
            row.state = state.model_dump(mode="json")
