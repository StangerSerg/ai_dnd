"""Offline model stub for structured decisions and basic narration."""

import json
import re
from collections.abc import AsyncIterator

from ai_dnd.ai.provider import CompletionRequest, ProviderResult
from ai_dnd.ai.schemas import Message, ToolCall, Usage

_ACTOR = re.compile(r"\(id: ([a-z0-9_]+)\): (.+)")
_SECTION_ITEM = re.compile(r"^- ([a-z0-9_]+):", re.MULTILINE)
_MOVE_WORDS = ("иду", "идём", "идем", "отправля", "go ", "go.", "head ", "travel")
_ATTACK_WORDS = ("атак", "нападаю", "напада", "бью", "attack", "fight")
_INSPECT_WORDS = ("осматр", "ищ", "изуч", "inspect", "search", "look for")
_INDEXED_ACTION = re.compile(r"^- input_index=(\d+), actor_id=([a-z0-9_]+): (.+)$", re.MULTILINE)


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
        english = any(
            message.role == "system" and "You narrate in English." in (message.content or "")
            for message in request.messages
        )
        # Задача сводки (раздел 9.3): маркер в системном промпте, без импорта game-слоя.
        summarizer = next(
            (
                message.content or ""
                for message in request.messages
                if message.role == "system" and "session summarizer" in (message.content or "")
            ),
            None,
        )
        if summarizer is not None:
            recap = (
                "(stub) Recap of earlier turns: key facts preserved."
                if "written in English" in summarizer
                else "(заглушка) Сводка ранних ходов: ключевые факты сохранены."
            )
            return self._text(request, recap)
        # Задача импорта (подэтап 2.5): маркер в системном промпте, без импорта importer-слоя.
        importer = next(
            (
                message.content or ""
                for message in request.messages
                if message.role == "system" and "scenario importer" in (message.content or "")
            ),
            None,
        )
        if importer is not None:
            return self._text(request, self._import_json(importer))
        if request.json_output:
            return self._text(request, self._decision_json(last))
        if last.role == "tool":
            prefix = "(stub) The engine responded" if english else "(заглушка) Движок ответил"
            return self._text(request, f"{prefix}: {_short(last.content)}.")
        if request.tools and request.tool_choice != "none" and last.role == "user":
            call = self._tool_call(last)
            if call is not None:
                return self._result(request, "", (call,))
        if last.role == "user" and "# Combat log since" in (last.content or ""):
            text = (
                "(stub) Combat round: blades clash, someone falls."
                if english
                else "(заглушка) Раунд боя: клинки звенят, кто-то падает."
            )
            return self._text(request, text)
        text = (
            "(stub) The game master describes the scene and waits for your actions."
            if english
            else "(заглушка) Ведущий описывает сцену и ждёт ваших действий."
        )
        return self._text(request, text)

    def _import_json(self, prompt: str) -> str:
        """Минимальный валидный ImportDraft: одна стартовая сцена и заметка-допущение."""
        russian = "in Russian" in prompt
        title = "(заглушка) Импортированный сценарий" if russian else "(stub) Imported scenario"
        synopsis = (
            "(заглушка) Краткий пересказ модуля: замените текстом автора."
            if russian
            else "(stub) Short module synopsis: replace with the author's text."
        )
        read_aloud = (
            "(заглушка) Вы стоите в начале приключения. Ведущий ждёт ваших действий."
            if russian
            else "(stub) You stand at the start of the adventure. The DM awaits your actions."
        )
        note = (
            "Заглушка не разбирает модуль: весь черновик — минимальный каркас, "
            "заполните его по тексту автора."
            if russian
            else "The stub does not parse the module: the draft is a minimal skeleton, "
            "fill it in from the author's text."
        )
        draft = {
            "scenario": {
                "scenario": {
                    "title": title,
                    "language": "ru" if russian else "en",
                    "ruleset": "dnd5e-srd@1",
                    "sheet_template": "srd5-basic@1",
                    "start_scene": "start",
                    "synopsis": synopsis,
                },
                "scenes": [{"id": "start", "title": title, "read_aloud": read_aloud}],
            },
            "notes": [{"kind": "assumption", "path": "scenario", "text": note}],
        }
        return json.dumps(draft, ensure_ascii=False)

    def _decision_json(self, message: Message) -> str:
        content = message.content or ""
        section = content.split("# Unresolved original inputs (authoritative indices)", 1)[-1]
        section = section.split("\n\n#", 1)[0]
        actions = _INDEXED_ACTION.findall(section)
        exits = _items(content, "Exits:")
        encounters = _items(content, "Encounters here:")
        choices: list[dict[str, object]] = []
        already_moved = "# Movement already completed in this turn\nTrue" in content
        move_found = (
            any(any(word in text.lower() for word in _MOVE_WORDS) for _, _, text in actions)
            and bool(exits)
            and not already_moved
        )
        for index, actor_id, action_text in actions:
            lowered = action_text.lower()
            choice: dict[str, object] = {
                "input_index": int(index),
                "actor_id": actor_id,
                "kind": "stay",
            }
            if any(word in lowered for word in _MOVE_WORDS) and exits and not already_moved:
                choice.update(
                    kind="move",
                    scene_id=exits[0],
                    continue_after=any(word in lowered for word in _INSPECT_WORDS),
                )
            elif move_found:
                choice["kind"] = "defer"
            elif any(word in lowered for word in _ATTACK_WORDS) and encounters:
                choice.update(kind="engage", encounter_id=encounters[0])
            elif any(word in lowered for word in _INSPECT_WORDS):
                choice.update(
                    kind="tool",
                    tool_name="request_check",
                    arguments={
                        "actor_id": actor_id,
                        "skill": "perception",
                        "difficulty": "medium",
                    },
                )
            choices.append(choice)
        return json.dumps({"actions": choices}, ensure_ascii=False)

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
