"""Тесты конвейера импорта: guards, structured-вызов, retry, проверка ссылок, стоимость.

ScriptedProvider подменяет DeepSeek: очередь ответов и запись запросов, без сети и денег.
"""

import json
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pytest

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.provider import CompletionRequest, LLMError, ProviderResult
from ai_dnd.ai.schemas import Usage
from ai_dnd.importer.pipeline import MAX_IMPORT_CHARS, ImportResult, import_scenario
from ai_dnd.importer.prompts import IMPORT_MARKER
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary

MODULE_TEXT = "Колодец забытых имён. Савелий ведёт героев к старому колодцу."


class ScriptedProvider:
    """Провайдер с очередью заготовленных ответов; пишет каждый запрос."""

    name = "deepseek"

    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        self.requests.append(request)
        if not self._answers:
            raise AssertionError("ScriptedProvider: ответы закончились")
        return ProviderResult(
            text=self._answers.pop(0),
            tool_calls=(),
            reasoning=None,
            finish_reason="stop",
            usage=Usage(input_tokens=1000, output_tokens=200),
            model="scripted",
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str | ProviderResult]:
        yield await self.complete(request)


def _draft_json(draft_data: dict[str, Any]) -> str:
    return json.dumps(draft_data, ensure_ascii=False)


async def _run(
    config: LLMConfig,
    provider: ScriptedProvider,
    ruleset: Ruleset,
    bestiary: Bestiary,
    *,
    text: str = MODULE_TEXT,
    language: str = "ru",
    template: str = "srd5-basic@1",
) -> ImportResult:
    return await import_scenario(
        LLMGateway(config, {"deepseek": provider}),
        text=text,
        language=language,
        ruleset=ruleset,
        bestiaries=(bestiary,),
        sheet_template=template,
    )


async def test_valid_draft_single_call(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    provider = ScriptedProvider([_draft_json(draft_data)])

    result = await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    assert result.issues == ()
    assert len(result.draft.notes) == 1
    assert result.draft.notes[0].kind == "assumption"
    assert len(result.responses) == 1
    assert len(provider.requests) == 1
    assert result.cost > 0
    assert result.cost == result.responses[0].cost


async def test_structured_request_routing_and_json_instruction(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    """Один structured-вызов задачи import: JSON-инструкция со схемой и json_output."""
    provider = ScriptedProvider([_draft_json(draft_data)])

    await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    request = provider.requests[0]
    assert request.model == llm_config.tasks["import"].model
    assert request.json_output is True
    assert any("JSON Schema" in (message.content or "") for message in request.messages)


async def test_prompt_marker_and_context_in_request(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    provider = ScriptedProvider([_draft_json(draft_data)])

    await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    system = provider.requests[0].messages[0].content or ""
    user = provider.requests[0].messages[1].content or ""
    assert IMPORT_MARKER in system
    assert "srd:skeleton" in user
    assert "perception" in user
    assert MODULE_TEXT in user


async def test_retry_invalid_json_with_feedback(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    """Невалидный JSON → шлюз возвращает модели текст ошибки → второй ответ валиден."""
    provider = ScriptedProvider(['{"scenario": 42}', _draft_json(draft_data)])

    result = await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    assert len(result.responses) == 2
    assert result.issues == ()
    assert len(provider.requests) == 2
    second = provider.requests[1].messages
    assert any("did not pass validation" in (message.content or "") for message in second)
    # История содержит прежний ответ модели — она видит, что именно исправляет.
    assert any(
        message.role == "assistant" and message.content == '{"scenario": 42}' for message in second
    )
    assert result.cost == sum((response.cost for response in result.responses), Decimal(0))


async def test_broken_refs_detected(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    draft_data["scenario"]["scenes"][0]["exits"] = ["nowhere"]
    draft_data["scenario"]["scenes"][0]["checks"][0]["on_success"]["reveal"] = ["ghost"]
    draft_data["scenario"]["npcs"][0]["statblock"] = "srd:dragon"
    provider = ScriptedProvider([_draft_json(draft_data)])

    result = await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    codes = {issue.code for issue in result.issues}
    assert {"unknown_scene", "unknown_statblock", "unknown_reveal"} <= codes


async def test_language_and_template_mismatch(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    draft_data["scenario"]["scenario"]["language"] = "en"
    draft_data["scenario"]["scenario"]["sheet_template"] = "srd5-basic@9"
    provider = ScriptedProvider([_draft_json(draft_data)])

    result = await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    by_code = {issue.code: issue for issue in result.issues}
    assert by_code["language_mismatch"].params == {"expected": "ru", "actual": "en"}
    assert by_code["template_mismatch"].params == {
        "expected": "srd5-basic@1",
        "actual": "srd5-basic@9",
    }


async def test_empty_text_guard(
    llm_config: LLMConfig, srd5_ruleset: Ruleset, srd_bestiary: Bestiary
) -> None:
    provider = ScriptedProvider([])

    with pytest.raises(ValueError, match="empty_document"):
        await _run(llm_config, provider, srd5_ruleset, srd_bestiary, text="   ")

    assert provider.requests == []


async def test_too_large_guard(
    llm_config: LLMConfig, srd5_ruleset: Ruleset, srd_bestiary: Bestiary
) -> None:
    provider = ScriptedProvider([])

    with pytest.raises(ValueError, match="import_text_too_large"):
        await _run(
            llm_config, provider, srd5_ruleset, srd_bestiary, text="я" * (MAX_IMPORT_CHARS + 1)
        )

    assert provider.requests == []


async def test_max_chars_boundary_accepted(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    """Ровно MAX_IMPORT_CHARS символов — ещё допустимо."""
    provider = ScriptedProvider([_draft_json(draft_data)])

    result = await _run(
        llm_config, provider, srd5_ruleset, srd_bestiary, text="я" * MAX_IMPORT_CHARS
    )

    assert len(result.responses) == 1


async def test_invalid_structured_output_exhausted(
    llm_config: LLMConfig, srd5_ruleset: Ruleset, srd_bestiary: Bestiary
) -> None:
    """Все ответы невалидны → LLMError после validation_attempts + 1 вызовов."""
    attempts = llm_config.retries.validation_attempts + 1
    provider = ScriptedProvider(["{}"] * attempts)

    with pytest.raises(LLMError, match="invalid_structured_output") as error:
        await _run(llm_config, provider, srd5_ruleset, srd_bestiary)

    assert error.value.code == "invalid_structured_output"
    assert len(provider.requests) == attempts


async def test_prompt_deterministic(
    llm_config: LLMConfig,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
    draft_data: dict[str, Any],
) -> None:
    """Два одинаковых запуска дают побайтово одинаковый запрос (включая JSON-схему)."""
    first = ScriptedProvider([_draft_json(draft_data)])
    second = ScriptedProvider([_draft_json(draft_data)])

    await _run(llm_config, first, srd5_ruleset, srd_bestiary)
    await _run(llm_config, second, srd5_ruleset, srd_bestiary)

    assert first.requests[0] == second.requests[0]
