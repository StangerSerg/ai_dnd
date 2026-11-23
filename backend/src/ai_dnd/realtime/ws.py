"""WebSocket-шлюз комнаты (архитектура, 12): досылка по seq, фильтр видимости, pub/sub.

Подключение: ``/ws/sessions/{id}?since=<seq>``. Без since досылка идёт от
``seat_reads.last_seen_seq``. Подписка на Redis-канал оформляется до чтения журнала,
поэтому событие между чтением и подпиской не теряется; дубли отсекаются по seq.
Клиент может подтверждать просмотр сообщением ``{"type": "ack", "seq": N}``.

Сообщения канала бывают двух видов (протокол — в broadcast.py): журнальные события
с seq/type/visibility/payload и эфемерные chunk-и стриминга рассказа DM
``{"type": "narration_chunk", "payload": {"text": ...}}`` без seq. Chunk отличается
от журнального сообщения отсутствием seq: он публичен (голос DM), пересылается всем
без фильтра видимости, без seq-дедупа и без записи в seat_reads; при переподключении
не досылается. Полный текст приходит финальным журнальным Narrated.
"""

import asyncio
import json
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio.client import PubSub
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.realtime import service
from ai_dnd.realtime.auth import GetCurrentUser
from ai_dnd.realtime.broadcast import channel_name
from ai_dnd.realtime.models import SeatRow
from ai_dnd.resources import Resources
from ai_dnd.rules.visibility import can_see

Sessions = async_sessionmaker[AsyncSession]


@dataclass
class _Stream:
    last_sent: int


def build_ws_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/sessions/{session_id}")
    async def session_room(
        websocket: WebSocket, session_id: uuid.UUID, since: int | None = None
    ) -> None:
        resources: Resources = websocket.app.state.resources
        sessions = resources.db_sessions
        try:
            user = await get_current_user(websocket)
        except Exception:
            await websocket.close(code=4401)
            return
        seat = await service.find_seat(sessions, session_id, user.user_id)
        if seat is None:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        start = since if since is not None else await service.last_seen(sessions, seat.id)
        stream = _Stream(last_sent=max(start, 0))
        pubsub = resources.redis.pubsub()
        channel = channel_name(session_id)
        await pubsub.subscribe(channel)
        forward = asyncio.create_task(
            _forward(websocket, pubsub, sessions, seat, stream, session_id)
        )
        reader = asyncio.create_task(_read_acks(websocket, sessions, seat.id))
        try:
            done, pending = await asyncio.wait(
                {forward, reader}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done | pending:
                with suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await task
        finally:
            await pubsub.unsubscribe(channel)
            # PubSub.aclose в redis-py без аннотаций типов.
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return router


async def _forward(
    websocket: WebSocket,
    pubsub: PubSub,
    sessions: Sessions,
    seat: SeatRow,
    stream: _Stream,
    session_id: uuid.UUID,
) -> None:
    backlog = await service.fetch_events(sessions, session_id, stream.last_sent)
    for row in backlog:
        await _send(websocket, sessions, seat, stream, service.event_out(row).model_dump())
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        event: dict[str, Any] = json.loads(message["data"])
        if "seq" not in event:
            # Эфемерное сообщение без журнала и seat_reads: chunk стриминга (публичный
            # голос DM) или control-сообщения (seat_update, host_transferred,
            # session_stopped, chat_message) — пересылаются всем как есть, без фильтра.
            await websocket.send_json(event)
            payload = event.get("payload")
            if (
                event.get("type") == "seat_update"
                and isinstance(payload, dict)
                and payload.get("seat_id") == str(seat.id)
                and payload.get("removed") is True
            ):
                # Кик этого места: сокет закрывается, повторный вход невозможен.
                await websocket.close(code=4403)
                return
            # Ведение передано: сокет прежнего хоста (dm-место сменило владельца,
            # иначе он продолжал бы получать dm-события) и сокет повышенного
            # игрока (его player-место удалено) закрываются — переподключение
            # получит актуальное место. Остальные участники продолжают сеанс.
            # В ai-режиме dm_seat_id/removed_seat_id — null: никто не теряет
            # место, сокеты не закрываются (клиенты сами обновят сессию).
            if event.get("type") == "host_transferred" and isinstance(payload, dict):
                closed_seat_ids = {
                    value
                    for value in (payload.get("dm_seat_id"), payload.get("removed_seat_id"))
                    if isinstance(value, str)
                }
                if str(seat.id) in closed_seat_ids:
                    await websocket.close(code=4403)
                    return
            continue
        await _send(websocket, sessions, seat, stream, event)


async def _send(
    websocket: WebSocket,
    sessions: Sessions,
    seat: SeatRow,
    stream: _Stream,
    event: dict[str, Any],
) -> None:
    seq: int = event["seq"]
    if seq <= stream.last_sent:
        return
    # Фильтр видимости — на сервере, для каждого получателя отдельно.
    if not can_see(event["visibility"], seat.actor_id, dm=seat.role == "dm"):
        return
    await websocket.send_json(event)
    stream.last_sent = seq
    await service.mark_seen(sessions, seat.id, seq)


async def _read_acks(websocket: WebSocket, sessions: Sessions, seat_id: uuid.UUID) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("type") == "ack":
            seq = message.get("seq")
            if isinstance(seq, int) and seq >= 0:
                await service.mark_seen(sessions, seat_id, seq)
