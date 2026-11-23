"""Рассылка событий комнаты через Redis pub/sub (архитектура, 12).

``BroadcastJournal`` оборачивает любой журнал: события сначала фиксируются внутренним
журналом, затем публикуются в канал ``session:<id>``. Номера seq берутся из last_seq
сессии после записи: блокировка комнаты на время хода (5.6) не даёт другому писателю
вклиниться между append и чтением last_seq.

Протокол канала ``session:<id>`` (JSON-объекты двух видов):

- журнальное событие: ``{"seq": N, "type": ..., "visibility": ..., "payload": {...}}`` —
  записано в журнал, досылается при переподключении, фильтруется по видимости;
- эфемерный chunk стриминга рассказа: ``{"type": "narration_chunk",
  "payload": {"text": "..."}}`` — без seq и visibility. Публичный голос DM (фильтр
  не нужен), в журнал и seat_reads не пишется, по seq не дедуплицируется и при
  переподключении не досылается. Полный текст рассказа приходит финальным
  журнальным событием ``Narrated`` (оно же — признак конца стрима).
"""

import json
import uuid
from collections.abc import Sequence
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.config import get_settings
from ai_dnd.game.events import JOURNAL_EVENT, JournalEvent, NpcMoved
from ai_dnd.game.journal import Journal
from ai_dnd.game.models import GameSessionRow
from ai_dnd.game.presence import scene_occupancy
from ai_dnd.game.session import SessionState
from ai_dnd.scenarios.store import load_game_content_from_db


def channel_name(session_id: uuid.UUID) -> str:
    """Канал комнаты сессии."""
    return f"session:{session_id}"


def npc_locations_key(session_id: uuid.UUID) -> str:
    """Производный Redis-кэш присутствия NPC: {scene_id: [npc_id, ...]}.

    Не источник правды: авторитет — state.npc_locations (PostgreSQL) поверх home
    сценария; кэш всегда пересобирается из state+content (rebuild_npc_locations).
    """
    return f"ai_dnd:npcloc:{session_id}"


async def rebuild_npc_locations(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    state: SessionState,
) -> None:
    """Пересобрать кэш эффективной занятости NPC и перезаписать ключ целиком."""
    async with sessions() as db:
        version_id = await db.scalar(
            select(GameSessionRow.scenario_version_id).where(GameSessionRow.id == session_id)
        )
    if version_id is None:
        await redis.delete(npc_locations_key(session_id))
        return
    content = await load_game_content_from_db(sessions, version_id, get_settings().content_dir)
    occupancy = scene_occupancy(content.scenario, state)
    await redis.set(npc_locations_key(session_id), json.dumps(occupancy, ensure_ascii=False))


async def publish_event(redis: Redis, session_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Опубликовать событие в канал комнаты."""
    await redis.publish(channel_name(session_id), json.dumps(event, ensure_ascii=False))


async def publish_chunk(redis: Redis, session_id: uuid.UUID, text: str) -> None:
    """Эфемерный chunk потокового рассказа DM: без seq, в журнал не пишется."""
    await publish_event(redis, session_id, {"type": "narration_chunk", "payload": {"text": text}})


async def publish_seat_update(
    redis: Redis,
    session_id: uuid.UUID,
    seat_id: uuid.UUID,
    *,
    muted: bool,
    removed: bool,
    mute_source: str | None = None,
) -> None:
    """Эфемерное control-сообщение модерации места: без seq, в журнал и seat_reads не пишется."""
    await publish_event(
        redis,
        session_id,
        {
            "type": "seat_update",
            "payload": {
                "seat_id": str(seat_id),
                "muted": muted,
                "removed": removed,
                "mute_source": mute_source,
            },
        },
    )


async def publish_host_transferred(
    redis: Redis,
    session_id: uuid.UUID,
    *,
    dm_seat_id: uuid.UUID | None,
    new_host_user_id: uuid.UUID,
    removed_seat_id: uuid.UUID | None,
) -> None:
    """Эфемерное control-сообщение передачи ведения стола: без seq, не журналируется.

    Отдельный тип (не seat_update removed=True): удаление собственного места
    фронтенд трактует как кик, а повышенный игрок должен узнать о новом dm-месте.
    В ai-режиме dm-места нет и никто не теряет место: dm_seat_id/removed_seat_id —
    null, сокеты не закрываются (клиенты сами обновят сессию).
    """
    await publish_event(
        redis,
        session_id,
        {
            "type": "host_transferred",
            "payload": {
                "session_id": str(session_id),
                "dm_seat_id": str(dm_seat_id) if dm_seat_id is not None else None,
                "new_host_user_id": str(new_host_user_id),
                "removed_seat_id": str(removed_seat_id) if removed_seat_id is not None else None,
            },
        },
    )


async def publish_chat_message(
    redis: Redis, session_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    """Эфемерное сообщение чата вне игры: без seq, история хранится отдельно (REST)."""
    await publish_event(redis, session_id, {"type": "chat_message", "payload": payload})


async def publish_session_stopped(redis: Redis, session_id: uuid.UUID, *, stopped: bool) -> None:
    """Эфемерное control-сообщение остановки/возобновления сессии: без seq, не журналируется."""
    await publish_event(
        redis,
        session_id,
        {
            "type": "session_stopped",
            "payload": {"session_id": str(session_id), "stopped": stopped},
        },
    )


async def publish_session_restarted(
    redis: Redis, session_id: uuid.UUID, *, state_version: int
) -> None:
    """Эфемерное control-сообщение рестарта стола после поражения: без seq, не журналируется.

    Журнал и состояние уже сброшены; клиентам нужно перечитать снапшот сессии с нуля.
    """
    await publish_event(
        redis,
        session_id,
        {
            "type": "session_restarted",
            "payload": {"session_id": str(session_id), "state_version": state_version},
        },
    )


class BroadcastJournal:
    """Журнал, который после записи рассылает события подписчикам комнаты."""

    def __init__(
        self,
        inner: Journal,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._sessions = sessions

    @property
    def session_id(self) -> uuid.UUID | None:
        return self._inner.session_id

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        await self._inner.append(events, state)
        session_id = self._inner.session_id
        if not events or session_id is None:
            return
        # Коммит перемещения NPC обновляет производный кэш присутствия целиком.
        if any(isinstance(event, NpcMoved) for event in events):
            await rebuild_npc_locations(self._redis, self._sessions, session_id, state)
        async with self._sessions() as db:
            last_seq = await db.scalar(
                select(GameSessionRow.last_seq).where(GameSessionRow.id == session_id)
            )
        if last_seq is None:
            raise LookupError(f"Нет игровой сессии {session_id}")
        first = last_seq - len(events) + 1
        for offset, event in enumerate(events):
            await publish_event(
                self._redis,
                session_id,
                {
                    "seq": first + offset,
                    "type": event.type,
                    "visibility": event.visibility,
                    "payload": JOURNAL_EVENT.dump_python(event, mode="json"),
                },
            )
