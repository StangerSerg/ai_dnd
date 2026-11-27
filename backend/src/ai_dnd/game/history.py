"""Restore a bounded public conversation tail from the durable event journal."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import SessionSetup


async def load_recent_history(
    sessions: async_sessionmaker[AsyncSession],
    row: GameSessionRow,
    setup: SessionSetup,
    *,
    covered: int,
    recent_turns: int,
    summary_every: int,
    backlog_limit: int,
) -> tuple[list[tuple[str, str]], int, int]:
    """Return (transcript, first ordinal, total public narrations) at ``last_seq``.

    Only the selected narration tail and events between those narrations are
    materialized. The indexed COUNT supplies the absolute ordinal used by
    SummaryUpdated, even when each web turn has a fresh GameMaster instance.
    """
    base = (
        GameEventRow.session_id == row.id,
        GameEventRow.seq <= row.last_seq,
        GameEventRow.type == "Narrated",
        GameEventRow.visibility == "public",
    )
    async with sessions() as db:
        total = int(
            await db.scalar(select(func.count()).select_from(GameEventRow).where(*base)) or 0
        )
        if covered > total:
            raise ValueError("Summary cursor exceeds public narration count")
        # In ordinary play the largest needed window is N + K - 1. If summary
        # has failed, retain every uncovered narration up to the safety limit.
        desired = max(recent_turns + summary_every - 1, total - covered)
        limit = min(recent_turns + backlog_limit, desired)
        recent = list(
            (
                await db.execute(
                    select(GameEventRow.seq, GameEventRow.payload)
                    .where(*base)
                    .order_by(GameEventRow.seq.desc())
                    .limit(limit + 1)
                )
            ).all()
        )
        boundary = recent[limit].seq if len(recent) > limit else 0
        narrated = list(reversed(recent[:limit]))
        if not narrated:
            return [], total, total
        intervening = list(
            (
                await db.execute(
                    select(GameEventRow.seq, GameEventRow.type, GameEventRow.payload)
                    .where(
                        GameEventRow.session_id == row.id,
                        GameEventRow.seq > boundary,
                        GameEventRow.seq <= row.last_seq,
                        GameEventRow.visibility == "public",
                        GameEventRow.type.in_(("PlayerActed", "RoundStarted", "CombatEnded")),
                    )
                    .order_by(GameEventRow.seq)
                )
            ).all()
        )

    first_turn = total - len(narrated)
    return _group_history(narrated, intervening, setup, first_turn), first_turn, total


async def load_history_batch(
    sessions: async_sessionmaker[AsyncSession],
    row: GameSessionRow,
    setup: SessionSetup,
    *,
    start_turn: int,
    limit: int,
) -> list[tuple[str, str]]:
    """Read one contiguous public narration batch starting at an absolute ordinal."""
    if limit < 1 or limit > 64:
        raise ValueError("History batch must contain 1..64 narrations")
    offset = max(0, start_turn - 1)
    async with sessions() as db:
        rows = list(
            (
                await db.execute(
                    select(GameEventRow.seq, GameEventRow.payload)
                    .where(
                        GameEventRow.session_id == row.id,
                        GameEventRow.seq <= row.last_seq,
                        GameEventRow.type == "Narrated",
                        GameEventRow.visibility == "public",
                    )
                    .order_by(GameEventRow.seq)
                    .offset(offset)
                    .limit(limit + (1 if start_turn else 0))
                )
            ).all()
        )
        boundary = rows.pop(0).seq if start_turn and rows else 0
        if len(rows) != limit:
            raise RuntimeError("History batch is incomplete")
        last_seq = rows[-1].seq
        intervening = list(
            (
                await db.execute(
                    select(GameEventRow.seq, GameEventRow.type, GameEventRow.payload)
                    .where(
                        GameEventRow.session_id == row.id,
                        GameEventRow.seq > boundary,
                        GameEventRow.seq <= last_seq,
                        GameEventRow.visibility == "public",
                        GameEventRow.type.in_(("PlayerActed", "RoundStarted", "CombatEnded")),
                    )
                    .order_by(GameEventRow.seq)
                )
            ).all()
        )
    return _group_history(rows, intervening, setup, start_turn)


def _group_history(
    narrated: Sequence[Any], intervening: Sequence[Any], setup: SessionSetup, first_turn: int
) -> list[tuple[str, str]]:
    history: list[tuple[str, str]] = []
    actions: list[str] = []
    combat = False
    event_index = 0
    for narration in narrated:
        while event_index < len(intervening) and intervening[event_index].seq < narration.seq:
            event = intervening[event_index]
            if event.type == "PlayerActed":
                actor_id = str(event.payload["actor_id"])
                seat = setup.seat(actor_id)
                name = seat.name if seat is not None else actor_id
                actions.append(f"- {name}: {event.payload['text']}")
            else:
                combat = True
            event_index += 1
        if actions:
            entry = "Players' actions:\n" + "\n".join(actions)
        elif combat:
            entry = "(Combat round)"
        elif first_turn + len(history) == 0:
            entry = "(Session start)"
        else:
            entry = "(Narration)"
        history.append((entry, str(narration.payload["text"])))
        actions = []
        combat = False
    return history
