"""Шлюз к языковым моделям (архитектура, раздел 10).

Игровая логика называет задачу — «narration», «dm_tools», «summary» — а шлюз сам
выбирает модель и её настройки по конфигурации, следит за таймаутами, повторяет вызов
при временных сбоях, считает стоимость и записывает каждый вызов.

    gateway = build_gateway(settings, recorder)
    response = await gateway.complete("dm_tools", messages, tools=tools)
    async for event in gateway.stream("narration", messages):
        ...
    summary = await gateway.structured("summary", messages, SessionSummary)
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from pydantic import BaseModel, ValidationError

from ai_dnd.ai.config import LLMConfig, TaskConfig
from ai_dnd.ai.provider import (
    CompletionRequest,
    LLMError,
    LLMProvider,
    LLMTransientError,
    ProviderResult,
)
from ai_dnd.ai.recorder import CallContext, CallRecord, CallRecorder
from ai_dnd.ai.schemas import (
    LLMResponse,
    Message,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolChoice,
    ToolSpec,
    Usage,
)


@dataclass(frozen=True, slots=True)
class Structured[T: BaseModel]:
    """Ответ, проверенный по схеме, и все вызовы, которые на него ушли."""

    value: T
    responses: tuple[LLMResponse, ...]

    @property
    def cost(self) -> Decimal:
        return sum((response.cost for response in self.responses), Decimal(0))


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LLMGateway:
    def __init__(
        self,
        config: LLMConfig,
        providers: Mapping[str, LLMProvider],
        recorder: CallRecorder | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._config = config
        self._providers = providers
        self._recorder = recorder
        self._sleep = sleep
        self._now = now

    @property
    def config(self) -> LLMConfig:
        return self._config

    async def complete(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: ToolChoice | None = None,
        json_output: bool = False,
        context: CallContext | None = None,
    ) -> LLMResponse:
        """Дождаться ответа модели целиком."""
        settings, provider = self._route(task)
        request = _request(settings, messages, tools, tool_choice, json_output)
        retries = self._config.retries
        started = perf_counter()

        attempt = 0
        while True:
            attempt += 1
            try:
                result = await provider.complete(request)
                break
            except LLMTransientError as error:
                if attempt >= retries.attempts:
                    await self._record_failure(task, settings, provider, started, error, context)
                    raise
                await self._sleep(retries.backoff_seconds * 2 ** (attempt - 1))
            except LLMError as error:
                await self._record_failure(task, settings, provider, started, error, context)
                raise

        response = self._response(task, settings, provider, result, started)
        await self._record_success(response, context)
        return response

    async def stream(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: ToolChoice | None = None,
        context: CallContext | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Отдавать текст по мере генерации; последним событием приходит полный ответ."""
        settings, provider = self._route(task)
        request = _request(settings, messages, tools, tool_choice, json_output=False)
        retries = self._config.retries
        started = perf_counter()

        attempt = 0
        while True:
            attempt += 1
            shown = False
            try:
                async for item in provider.stream(request):
                    if isinstance(item, str):
                        shown = True
                        yield TextDelta(text=item)
                        continue
                    response = self._response(task, settings, provider, item, started)
                    await self._record_success(response, context)
                    yield StreamEnd(response=response)
                    return
                raise LLMError("empty_stream")
            except LLMTransientError as error:
                # Повторить можно, только пока игрокам ничего не показано:
                # иначе текст начался бы заново.
                if shown or attempt >= retries.attempts:
                    await self._record_failure(task, settings, provider, started, error, context)
                    raise
                await self._sleep(retries.backoff_seconds * 2 ** (attempt - 1))
            except LLMError as error:
                await self._record_failure(task, settings, provider, started, error, context)
                raise

    async def structured[T: BaseModel](
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        context: CallContext | None = None,
    ) -> Structured[T]:
        """Ответ в виде JSON, проверенный Pydantic-схемой.

        Ответам модели без проверки не доверяем. Если JSON не прошёл проверку, модель
        получает текст ошибки и исправляет ответ — столько раз, сколько разрешает
        конфигурация. Не получилось — ``LLMError("invalid_structured_output")``.
        """
        history = [*messages, Message.user(_json_instruction(schema))]
        responses: list[LLMResponse] = []
        problem = ""
        for _ in range(self._config.retries.validation_attempts + 1):
            response = await self.complete(task, history, json_output=True, context=context)
            responses.append(response)
            try:
                value = schema.model_validate_json(response.text)
            except ValidationError as error:
                problem = _validation_summary(error)
                history = [
                    *history,
                    response.as_message(),
                    Message.user(
                        "The JSON did not pass validation:\n"
                        f"{problem}\n"
                        "Return the corrected JSON object only."
                    ),
                ]
                continue
            return Structured(value=value, responses=tuple(responses))
        raise LLMError("invalid_structured_output", problem)

    # --- Внутреннее ---

    def _route(self, task: str) -> tuple[TaskConfig, LLMProvider]:
        settings = self._config.tasks.get(task)
        if settings is None:
            raise LLMError("unknown_task", task)
        provider_name = self._config.models[settings.model].provider
        provider = self._providers.get(provider_name)
        if provider is None:
            # Обычная причина — не задан ключ провайдера.
            raise LLMError("provider_not_configured", provider_name)
        return settings, provider

    def _response(
        self,
        task: str,
        settings: TaskConfig,
        provider: LLMProvider,
        result: ProviderResult,
        started: float,
    ) -> LLMResponse:
        prices = self._config.prices_for(settings.model, self._now())
        return LLMResponse(
            text=result.text,
            tool_calls=result.tool_calls,
            reasoning=result.reasoning,
            finish_reason=result.finish_reason,
            usage=result.usage,
            cost=prices.cost(result.usage),
            task=task,
            provider=provider.name,
            model=settings.model,
            latency_ms=_elapsed_ms(started),
        )

    async def _record_success(self, response: LLMResponse, context: CallContext | None) -> None:
        if self._recorder is None:
            return
        await self._recorder.record(
            CallRecord(
                task=response.task,
                provider=response.provider,
                model=response.model,
                usage=response.usage,
                cost=response.cost,
                latency_ms=response.latency_ms,
                context=context or CallContext(),
            )
        )

    async def _record_failure(
        self,
        task: str,
        settings: TaskConfig,
        provider: LLMProvider,
        started: float,
        error: LLMError,
        context: CallContext | None,
    ) -> None:
        if self._recorder is None:
            return
        await self._recorder.record(
            CallRecord(
                task=task,
                provider=provider.name,
                model=settings.model,
                usage=Usage(),
                cost=Decimal(0),
                latency_ms=_elapsed_ms(started),
                status="error",
                error=error.code,
                context=context or CallContext(),
            )
        )


# def build_gateway(settings: Settings, recorder: CallRecorder | None = None) -> LLMGateway:
#     """Шлюз по настройкам приложения. Провайдер без ключа просто не подключается."""
#     config = load_llm_config(settings.llm_config)
#     providers: dict[str, LLMProvider] = {}
#     key = settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else ""
#     if key and "deepseek" in config.providers:
#         providers["deepseek"] = DeepSeekProvider(key, config.providers["deepseek"].base_url)
#     return LLMGateway(config, providers, recorder)


def _request(
    settings: TaskConfig,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec],
    tool_choice: ToolChoice | None,
    json_output: bool,
) -> CompletionRequest:
    return CompletionRequest(
        model=settings.model,
        messages=tuple(messages),
        tools=tuple(tools),
        tool_choice=tool_choice,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        thinking=settings.thinking,
        reasoning_effort=settings.reasoning_effort,
        json_output=json_output,
        timeout=settings.timeout,
    )


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _json_instruction(schema: type[BaseModel]) -> str:
    # Промпты пишутся на английском: модели следуют им надёжнее (архитектура, раздел 18.3).
    # Ключи схемы отсортированы, чтобы текст запроса был одним и тем же от вызова к вызову.
    rendered = json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)
    return (
        "Respond with a single JSON object and nothing else. "
        f"The object must match this JSON Schema:\n{rendered}"
    )


def _validation_summary(error: ValidationError) -> str:
    """Короткий список проблем — без входных данных, чтобы не раздувать запрос."""
    lines = [
        f"- {'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
        for issue in error.errors(include_url=False, include_input=False)
    ]
    return "\n".join(lines[:20])
