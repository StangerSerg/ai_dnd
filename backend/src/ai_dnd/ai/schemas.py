"""Единый формат запросов и ответов языковых моделей (архитектура, раздел 10).

Игровая логика работает только с этими типами и не знает, какой провайдер за ними
стоит. Ответ всегда одного вида: текст, вызовы инструментов, расход токенов, стоимость.
"""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]
# Какой инструмент вызывать: модель решает сама, инструменты запрещены или вызов обязателен.
ToolChoice = Literal["auto", "none", "required"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCall(_Frozen):
    """Вызов инструмента, о котором просит модель."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Аргументы в том виде, в каком их прислала модель.
    raw_arguments: str = ""
    # Модель прислала не JSON: что именно не разобралось. Тогда ``arguments`` пуст.
    error: str | None = None


class Message(_Frozen):
    role: Role
    content: str | None = None
    # У ответа модели: какие инструменты она вызвала.
    tool_calls: tuple[ToolCall, ...] = ()
    # У результата инструмента: на какой вызов это ответ.
    tool_call_id: str | None = None
    # Размышления модели. Некоторым провайдерам их нужно возвращать в следующих
    # запросах, когда в разговоре есть инструменты.
    reasoning: str | None = None

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def tool(cls, call: ToolCall, content: str) -> "Message":
        """Результат инструмента — ответ на конкретный вызов."""
        return cls(role="tool", content=content, tool_call_id=call.id)


class ToolSpec(_Frozen):
    """Инструмент, который можно предложить модели."""

    name: str
    description: str
    # Параметры в виде JSON Schema.
    parameters: dict[str, Any]

    @classmethod
    def from_model(cls, name: str, description: str, model: type[BaseModel]) -> "ToolSpec":
        """Инструмент, параметры которого описаны Pydantic-моделью."""
        return cls(name=name, description=description, parameters=model.model_json_schema())


class Usage(_Frozen):
    """Расход токенов за один вызов."""

    input_tokens: int = 0
    # Сколько входных токенов взято из кэша провайдера — они стоят в разы дешевле.
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # Сколько выходных токенов ушло на размышления (входят в output_tokens).
    reasoning_tokens: int = 0

    @property
    def cache_share(self) -> float:
        """Доля входа, взятая из кэша: от 0 до 1. Упала — что-то сломало начало промпта."""
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0


class LLMResponse(_Frozen):
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning: str | None = None
    # Почему модель остановилась: stop, tool_calls, length (упёрлась в лимит) и так далее.
    finish_reason: str
    usage: Usage
    # Стоимость вызова в долларах по ценам из конфигурации.
    cost: Decimal
    task: str
    provider: str
    model: str
    latency_ms: int

    def as_message(self) -> Message:
        """Ответ как сообщение истории — чтобы продолжить разговор после инструментов."""
        return Message(
            role="assistant",
            content=self.text or None,
            tool_calls=self.tool_calls,
            reasoning=self.reasoning,
        )


class TextDelta(_Frozen):
    """Очередной кусочек текста при потоковой выдаче."""

    type: Literal["text"] = "text"
    text: str


class StreamEnd(_Frozen):
    """Поток закончен: полный ответ с расходом и стоимостью."""

    type: Literal["end"] = "end"
    response: LLMResponse


StreamEvent = TextDelta | StreamEnd
