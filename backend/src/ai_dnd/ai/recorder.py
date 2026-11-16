"""Запись вызовов языковых моделей.

Шлюз сообщает о каждом вызове — удачном и неудачном — тому, кто умеет записывать.
В приложении это таблица ``llm_calls``, в консоли и при отладке — список в памяти.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.ai.models import LlmCall
from ai_dnd.ai.schemas import Usage


@dataclass(frozen=True, slots=True)
class CallContext:
    """К чему относится вызов: игровая сессия и тот, кто за него платит."""

    session_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CallRecord:
    task: str
    provider: str
    model: str
    usage: Usage
    cost: Decimal
    latency_ms: int
    status: str = "ok"
    error: str | None = None
    context: CallContext = field(default_factory=CallContext)


class CallRecorder(Protocol):
    async def record(self, call: CallRecord) -> None: ...


class MemoryRecorder:
    """Записи в памяти: для консольного клиента, отладки и замеров стоимости."""

    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    async def record(self, call: CallRecord) -> None:
        self.calls.append(call)

    @property
    def total_cost(self) -> Decimal:
        return sum((call.cost for call in self.calls), Decimal(0))


class CompositeRecorder:
    """Записывает каждый вызов сразу в несколько мест: например, в память и в базу."""

    def __init__(self, *recorders: CallRecorder) -> None:
        self._recorders = recorders

    async def record(self, call: CallRecord) -> None:
        for recorder in self._recorders:
            await recorder.record(call)


class DbRecorder:
    """Записи в таблицу ``llm_calls``. Каждая запись — в своей короткой транзакции:
    учёт расходов не должен пропасть, если ход игры откатится."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, call: CallRecord) -> None:
        async with self._sessions() as session:
            session.add(
                LlmCall(
                    session_id=call.context.session_id,
                    user_id=call.context.user_id,
                    task=call.task,
                    provider=call.provider,
                    model=call.model,
                    input_tokens=call.usage.input_tokens,
                    cached_input_tokens=call.usage.cached_input_tokens,
                    output_tokens=call.usage.output_tokens,
                    reasoning_tokens=call.usage.reasoning_tokens,
                    cost=call.cost,
                    latency_ms=call.latency_ms,
                    status=call.status,
                    error=call.error,
                )
            )
            await session.commit()
