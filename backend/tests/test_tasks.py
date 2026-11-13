from ai_dnd.tasks import ping


async def test_ping_answers_with_message() -> None:
    # Вызов задачи напрямую выполняет её в текущем процессе, без брокера.
    assert await ping("hello") == "pong: hello"
