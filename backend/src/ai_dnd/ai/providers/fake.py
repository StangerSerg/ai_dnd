"""Модель-заглушка: проверить цикл ведущего без денег и без сети.

Отвечает по простым правилам, глядя на последнее сообщение:

- после ответа инструмента — короткий «рассказ» об исходе;
- на ход игроков — вызывает инструмент: «иду/go» — ``change_scene`` к первому выходу,
  «атакую/attack» — ``start_combat`` с первым столкновением сцены, иначе —
  ``request_check`` на Восприятие для первого игрока;
- в остальных случаях (начало сессии, описание боя) — короткий текст.

Качество рассказа заглушка не проверяет — только то, что все части цикла связаны.
"""

import json
import re
from collections.abc import AsyncIterator

from ai_dnd.ai.provider import CompletionRequest, ProviderResult
from ai_dnd.ai.schemas import Message, ToolCall, Usage

_ACTOR = re.compile(r"\(id: ([a-z0-9_]+)\): (.+)")
_SECTION_ITEM = re.compile(r"^- ([a-z0-9_]+):", re.MULTILINE)
_MOVE_WORDS = ("иду", "идём", "идем", "отправля", "go ", "go.", "head ", "travel")
_ATTACK_WORDS = ("атак", "нападаю", "напада", "бью", "attack", "fight")


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._calls = 0

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        return self._answer(request)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str | ProviderResult]:
        result = self._answer(request)
        for word in re.findall(r"\S+\s*", result.text):
            yield word
        yield result

    def _answer(self, request: CompletionRequest) -> ProviderResult:
        self._calls += 1
        last = request.messages[-1]
        if last.role == "tool":
            return self._text(request, f"(заглушка) Движок ответил: {_short(last.content)}.")
        if request.tools and request.tool_choice != "none" and last.role == "user":
            call = self._tool_call(last)
            if call is not None:
                return self._result(request, "", (call,))
        if last.role == "user" and (last.content or "").startswith("# Combat log"):
            return self._text(request, "(заглушка) Раунд боя: клинки звенят, кто-то падает.")
        return self._text(request, "(заглушка) Ведущий описывает сцену и ждёт ваших действий.")

    def _tool_call(self, message: Message) -> ToolCall | None:
        content = message.content or ""
        if "# Players' actions" not in content:
            return None
        actions = _ACTOR.findall(content.split("# Players' actions", 1)[1])
        if not actions:
            return None
        actor_id, text = actions[0]
        lowered = text.lower()
        if any(word in lowered for word in _MOVE_WORDS):
            exits = _items(content, "Exits:")
            if exits:
                return self._call("change_scene", {"scene_id": exits[0]})
        if any(word in lowered for word in _ATTACK_WORDS):
            encounters = _items(content, "Encounters here:")
            if encounters:
                return self._call("start_combat", {"encounter_id": encounters[0]})
        return self._call(
            "request_check",
            {"actor_id": actor_id, "skill": "perception", "difficulty": "medium"},
        )

    def _call(self, name: str, arguments: dict[str, object]) -> ToolCall:
        raw = json.dumps(arguments)
        return ToolCall(id=f"fake_{self._calls}", name=name, arguments=arguments, raw_arguments=raw)

    def _text(self, request: CompletionRequest, text: str) -> ProviderResult:
        return self._result(request, text, ())

    def _result(
        self, request: CompletionRequest, text: str, calls: tuple[ToolCall, ...]
    ) -> ProviderResult:
        prompt = sum(len(message.content or "") for message in request.messages)
        return ProviderResult(
            text=text,
            tool_calls=calls,
            reasoning=None,
            finish_reason="tool_calls" if calls else "stop",
            # Грубая оценка: четыре символа на токен.
            usage=Usage(input_tokens=prompt // 4, output_tokens=max(1, len(text) // 4)),
            model="fake",
        )


def _items(content: str, header: str) -> list[str]:
    if header not in content:
        return []
    section = content.split(header, 1)[1].split("\n\n", 1)[0]
    return _SECTION_ITEM.findall(section)


def _short(text: str | None) -> str:
    text = text or ""
    return text if len(text) <= 160 else text[:157] + "..."
