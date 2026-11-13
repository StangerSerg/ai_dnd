"""Служебные команды для разработки.

uv run python -m ai_dnd.devtools ping-worker
"""

import argparse
import asyncio
import sys

from taskiq.exceptions import TaskiqResultTimeoutError

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ai_dnd.devtools")
    commands = parser.add_subparsers(dest="command", required=True)
    ping_parser = commands.add_parser("ping-worker", help="проверить, что воркер выполняет задачи")
    ping_parser.add_argument("--timeout", type=float, default=10.0, help="сколько ждать, секунд")

    args = parser.parse_args()
    if args.command == "ping-worker":
        sys.exit(asyncio.run(ping_worker(args.timeout)))


if __name__ == "__main__":
    main()
