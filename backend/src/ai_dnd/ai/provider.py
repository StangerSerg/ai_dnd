"""Интерфейс провайдера языковых моделей.

Новый провайдер — это новый класс с двумя методами. Шлюз и игровая логика при этом
не меняются (архитектура, раздел 10).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from ai_dnd.ai.schemas import Message, ToolCall, ToolChoice, ToolSpec, Usage


class LLMError(Exception):
    """Вызов модели не удался. ``code`` — короткая причина для журнала и интерфейса."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class LLMTransientError(LLMError):
    """Временный сбой: таймаут, обрыв связи, перегрузка провайдера. Вызов стоит повторить."""


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Запрос к модели — уже с конкретной моделью и её настройками."""

    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    tool_choice: ToolChoice | None = None
    max_tokens: int = 1024
    temperature: float | None = None
    # Размышления перед ответом: точнее, но дольше и дороже.
    thinking: bool = False
    reasoning_effort: Literal["low", "high", "max"] | None = None
    # Ответ должен быть одним JSON-объектом.
    json_output: bool = False
    timeout: float = 60.0


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Ответ провайдера до подсчёта стоимости."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    reasoning: str | None
    finish_reason: str
    usage: Usage
    # Модель, которая ответила на самом деле (провайдер может подставить другую версию).
    model: str


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        """Дождаться ответа целиком."""
        ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[str | ProviderResult]:
        """Отдавать текст по мере генерации; последним приходит полный ответ."""
        ...
