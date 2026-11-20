"""Адаптер DeepSeek.

У DeepSeek API в формате OpenAI, поэтому используется пакет ``openai`` со своим
``base_url``. Особенности DeepSeek, спрятанные здесь:

- режим размышлений включён по умолчанию и переключается полем ``thinking``;
- размышления приходят в ``reasoning_content`` и, если в разговоре есть инструменты,
  должны возвращаться в следующих запросах — иначе API отвечает ошибкой 400;
- сколько входа взято из кэша, сообщает поле ``prompt_cache_hit_tokens``.
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx2
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.completion_usage import CompletionUsage

from ai_dnd.ai.provider import CompletionRequest, LLMError, LLMTransientError, ProviderResult
from ai_dnd.ai.schemas import Message, ToolCall, Usage

DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL) -> None:
        # Повторы делает шлюз — одинаково для всех провайдеров.
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        try:
            response = await self._client.chat.completions.create(
                stream=False, **_parameters(request)
            )
        except openai.OpenAIError as error:
            raise _translate(error) from error

        choice = response.choices[0]
        message = choice.message
        return ProviderResult(
            text=message.content or "",
            tool_calls=tuple(
                _tool_call(call.id, call.function.name, call.function.arguments)
                for call in message.tool_calls or ()
                if isinstance(call, ChatCompletionMessageToolCall)
            ),
            reasoning=getattr(message, "reasoning_content", None) or None,
            finish_reason=choice.finish_reason or "unknown",
            usage=_usage(response.usage),
            model=response.model,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str | ProviderResult]:
        text: list[str] = []
        reasoning: list[str] = []
        # Вызовы инструментов приходят по кусочкам: собираем их по номеру.
        calls: dict[int, dict[str, str]] = {}
        finish_reason = "unknown"
        usage: CompletionUsage | None = None
        model = request.model

        try:
            chunks = await self._client.chat.completions.create(
                stream=True, stream_options={"include_usage": True}, **_parameters(request)
            )
            async for chunk in chunks:
                model = chunk.model or model
                if chunk.usage is not None:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                thought = getattr(delta, "reasoning_content", None)
                if thought:
                    reasoning.append(thought)
                for part in delta.tool_calls or ():
                    call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    if part.id:
                        call["id"] = part.id
                    if part.function is not None:
                        call["name"] += part.function.name or ""
                        call["arguments"] += part.function.arguments or ""
                if delta.content:
                    text.append(delta.content)
                    yield delta.content
        except openai.OpenAIError as error:
            raise _translate(error) from error

        yield ProviderResult(
            text="".join(text),
            tool_calls=tuple(
                _tool_call(call["id"], call["name"], call["arguments"])
                for _, call in sorted(calls.items())
            ),
            reasoning="".join(reasoning) or None,
            finish_reason=finish_reason,
            usage=_usage(usage),
            model=model,
        )


# --- Запрос ---


def _parameters(request: CompletionRequest) -> dict[str, Any]:
    with_tools = bool(request.tools)
    parameters: dict[str, Any] = {
        "model": request.model,
        "messages": [_message(message, with_reasoning=with_tools) for message in request.messages],
        "max_tokens": request.max_tokens,
        "timeout": request.timeout,
        "extra_body": {"thinking": {"type": "enabled" if request.thinking else "disabled"}},
    }
    if request.thinking and request.reasoning_effort is not None:
        parameters["reasoning_effort"] = request.reasoning_effort
    if request.temperature is not None and not request.thinking:
        # В режиме размышлений температура игнорируется.
        parameters["temperature"] = request.temperature
    if with_tools:
        parameters["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
        if request.tool_choice is not None:
            parameters["tool_choice"] = request.tool_choice
    if request.json_output:
        parameters["response_format"] = {"type": "json_object"}
    return parameters


def _message(message: Message, *, with_reasoning: bool) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "assistant":
        if message.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments
                        or json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if with_reasoning and message.reasoning:
            data["reasoning_content"] = message.reasoning
    return data


# --- Ответ ---


def _tool_call(call_id: str, name: str, raw_arguments: str) -> ToolCall:
    try:
        arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as error:
        return ToolCall(id=call_id, name=name, raw_arguments=raw_arguments, error=str(error))
    if not isinstance(arguments, dict):
        return ToolCall(
            id=call_id, name=name, raw_arguments=raw_arguments, error="аргументы — не объект"
        )
    return ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments=raw_arguments)


def _usage(usage: CompletionUsage | None) -> Usage:
    if usage is None:
        return Usage()
    cached = getattr(usage, "prompt_cache_hit_tokens", None)
    if cached is None and usage.prompt_tokens_details is not None:
        cached = usage.prompt_tokens_details.cached_tokens
    details = usage.completion_tokens_details
    return Usage(
        input_tokens=usage.prompt_tokens,
        cached_input_tokens=cached or 0,
        output_tokens=usage.completion_tokens,
        reasoning_tokens=(details.reasoning_tokens or 0) if details is not None else 0,
    )


# --- Ошибки ---


def _translate(error: openai.OpenAIError) -> LLMError:
    """Ошибка SDK в наших терминах: что стоит повторить, а что бесполезно."""
    detail = str(error)
    if isinstance(error, openai.APITimeoutError):
        return LLMTransientError("timeout", detail)
    if isinstance(error, openai.APIConnectionError):
        return LLMTransientError("connection", detail)
    if isinstance(error, openai.RateLimitError):
        return LLMTransientError("rate_limited", detail)
    if isinstance(error, openai.AuthenticationError):
        return LLMError("auth", detail)
    if isinstance(error, openai.APIStatusError):
        if error.status_code >= 500:
            return LLMTransientError("provider_unavailable", detail)
        if error.status_code == 402:
            return LLMError("insufficient_balance", detail)
        return LLMError("bad_request", detail)
    return LLMError("provider_error", detail)


# --- Баланс аккаунта ---


async def fetch_balance(api_key: str, base_url: str = DEFAULT_BASE_URL) -> Decimal:
    """Баланс аккаунта в USD: GET /user/balance — endpoint вне OpenAI-совместимого API.

    Ошибки сети и HTTP (включая 401) пробрасываются: решение о них принимает watcher.
    """
    async with httpx2.AsyncClient(base_url=base_url, timeout=10.0) as client:
        response = await client.get("/user/balance", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        data: Any = response.json()
    total = Decimal(0)
    for info in data.get("balance_infos") or []:
        if info.get("currency") == "USD":
            total += Decimal(str(info.get("total_balance", "0")))
    return total
