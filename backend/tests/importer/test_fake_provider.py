"""Тесты fake-ветки импорта: маркер «scenario importer» и язык по «in Russian»."""

import json

from ai_dnd.ai.provider import CompletionRequest, ProviderResult
from ai_dnd.ai.providers.fake import FakeProvider
from ai_dnd.ai.schemas import Message
from ai_dnd.importer.prompts import import_system_prompt
from ai_dnd.importer.schema import ImportDraft


def _request(language: str, *, json_output: bool = True) -> CompletionRequest:
    return CompletionRequest(
        model="fake",
        messages=(
            Message.system(import_system_prompt(language)),
            Message.user("# Source module text\nТекст модуля."),
        ),
        json_output=json_output,
    )


async def test_fake_import_russian() -> None:
    result = await FakeProvider().complete(_request("ru"))

    draft = ImportDraft.model_validate_json(result.text)
    assert draft.scenario.scenario.language == "ru"
    assert draft.scenario.scenario.title.startswith("(заглушка)")
    assert draft.scenario.scenario.start_scene == "start"
    assert len(draft.notes) == 1
    assert draft.notes[0].kind == "assumption"
    assert result.model == "fake"
    assert result.finish_reason == "stop"


async def test_fake_import_english() -> None:
    result = await FakeProvider().complete(_request("en"))

    draft = ImportDraft.model_validate_json(result.text)
    assert draft.scenario.scenario.language == "en"
    assert draft.scenario.scenario.title.startswith("(stub)")


async def test_importer_marker_wins_over_decision_json() -> None:
    """Маркер импорта в системном промпте важнее обычного json_output-режима."""
    request = CompletionRequest(
        model="fake",
        messages=(
            Message.system(import_system_prompt("ru")),
            Message.user("# Players' actions\n- input_index=0, actor_id=bran: осматриваю площадь"),
        ),
        json_output=True,
    )

    result = await FakeProvider().complete(request)

    # Ответ — черновик импорта, а не решение {"actions": ...}.
    assert ImportDraft.model_validate_json(result.text).scenario.scenario.language == "ru"


async def test_without_marker_no_import_draft() -> None:
    """Без маркера импорта fake отвечает обычным решением, а не черновиком."""
    request = CompletionRequest(
        model="fake",
        messages=(Message.user("# Players' actions\n- input_index=0, actor_id=bran: жду"),),
        json_output=True,
    )

    result = await FakeProvider().complete(request)

    assert "actions" in json.loads(result.text)


async def test_fake_import_stream_assembles_draft() -> None:
    provider = FakeProvider()
    items = [item async for item in provider.stream(_request("ru"))]

    assert isinstance(items[-1], ProviderResult)
    text = "".join(item for item in items if isinstance(item, str))
    assert text == items[-1].text
    assert ImportDraft.model_validate_json(text).scenario.scenario.language == "ru"
