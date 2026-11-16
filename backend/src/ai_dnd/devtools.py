"""Служебные команды для разработки.

uv run python -m ai_dnd.devtools ping-worker
uv run python -m ai_dnd.devtools llm-check
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from taskiq.exceptions import TaskiqResultTimeoutError

from ai_dnd.ai.gateway import LLMGateway, build_gateway
from ai_dnd.ai.models import LlmCall
from ai_dnd.ai.provider import LLMError
from ai_dnd.ai.recorder import DbRecorder
from ai_dnd.ai.schemas import LLMResponse, Message, StreamEnd, TextDelta, ToolSpec
from ai_dnd.config import get_settings
from ai_dnd.db.engine import create_engine, create_session_factory
from ai_dnd.tasks import ping
from ai_dnd.worker import broker


async def ping_worker(wait_seconds: float) -> int:
    """Отправить проверочную задачу и дождаться ответа воркера."""
    await broker.startup()
    try:
        task = await ping.kiq("hello from devtools")
        result = await task.wait_result(timeout=wait_seconds)
    except TaskiqResultTimeoutError:
        print(f"Воркер не ответил за {wait_seconds:g} с. Он запущен?", file=sys.stderr)
        return 1
    finally:
        await broker.shutdown()

    if result.is_err:
        print(f"Задача завершилась ошибкой: {result.error}", file=sys.stderr)
        return 1
    print(f"Воркер ответил: {result.return_value}")
    return 0


class _CheckArguments(BaseModel):
    skill: str = Field(description="Skill to check, for example stealth or perception")
    difficulty: Literal["easy", "medium", "hard"]


async def llm_check() -> int:
    """Потоковый запрос с вызовом инструмента через шлюз и запись в ``llm_calls``.

    Тратит доли цента с ключа DeepSeek из настроек.
    """
    settings = get_settings()
    engine = create_engine(settings)
    gateway = build_gateway(settings, DbRecorder(create_session_factory(engine)))
    tool = ToolSpec.from_model(
        "request_check",
        "Ask the game engine to roll a skill check for the player character.",
        _CheckArguments,
    )
    messages = [
        Message.system(
            "You are a game master. When the player attempts something risky, call "
            "request_check first, then describe the outcome in one or two sentences."
        ),
        Message.user("I try to sneak past the sleeping guard."),
    ]
    try:
        first = await _stream_to_console(gateway, messages, (tool,))
        if not first.tool_calls:
            print("Модель не вызвала инструмент.", file=sys.stderr)
            return 1
        call = first.tool_calls[0]
        print(f"Вызов инструмента: {call.name}({json.dumps(call.arguments, ensure_ascii=False)})")

        engine_result = {"roll": 12, "dc": 15, "outcome": "failure"}
        messages = [*messages, first.as_message(), Message.tool(call, json.dumps(engine_result))]
        await _stream_to_console(gateway, messages, (tool,))

        rows = await _last_llm_calls(engine, 2)
    except LLMError as error:
        print(f"Вызов не удался: {error}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print("\nПоследние записи в llm_calls:")
    for row in reversed(rows):
        share = row.cached_input_tokens / row.input_tokens if row.input_tokens else 0.0
        print(
            f"  #{row.id} {row.task} {row.model}: вход {row.input_tokens} "
            f"(из кэша {share:.0%}), выход {row.output_tokens}, ${row.cost}, "
            f"{row.latency_ms} мс, {row.status}"
        )
    return 0


async def _stream_to_console(
    gateway: LLMGateway, messages: Sequence[Message], tools: Sequence[ToolSpec]
) -> LLMResponse:
    async for event in gateway.stream("dm_tools", messages, tools=tools):
        if isinstance(event, TextDelta):
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif isinstance(event, StreamEnd):
            response = event.response
            if response.text:
                sys.stdout.write("\n")
            print(
                f"[{response.finish_reason}] токены: {response.usage.input_tokens} на входе "
                f"({response.usage.cache_share:.0%} из кэша), {response.usage.output_tokens} "
                f"на выходе; ${response.cost}"
            )
            return response
    raise LLMError("empty_stream")


async def _last_llm_calls(engine: AsyncEngine, limit: int) -> list[LlmCall]:
    async with create_session_factory(engine)() as session:
        result = await session.scalars(select(LlmCall).order_by(LlmCall.id.desc()).limit(limit))
        return list(result)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ai_dnd.devtools")
    commands = parser.add_subparsers(dest="command", required=True)
    ping_parser = commands.add_parser("ping-worker", help="проверить, что воркер выполняет задачи")
    ping_parser.add_argument("--timeout", type=float, default=10.0, help="сколько ждать, секунд")
    commands.add_parser(
        "llm-check", help="проверить шлюз ИИ: поток, инструмент, запись в llm_calls"
    )

    args = parser.parse_args()
    if args.command == "ping-worker":
        sys.exit(asyncio.run(ping_worker(args.timeout)))
    if args.command == "llm-check":
        sys.exit(asyncio.run(llm_check()))


if __name__ == "__main__":
    main()
