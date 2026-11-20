"""Тесты отчёта импорта: счётчики, секции, YAML round-trip и имена файлов."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_dnd.ai.schemas import LLMResponse
from ai_dnd.importer.pipeline import ImportResult
from ai_dnd.importer.report import entity_counts, render_report, write_result
from ai_dnd.importer.schema import ImportDraft
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary, Scenario
from ai_dnd.scenarios.service import ScenarioIssue, find_scenario_issues


@pytest.fixture
def import_result(
    draft_data: dict[str, Any], make_response: Callable[[Decimal], LLMResponse]
) -> ImportResult:
    return ImportResult(
        draft=ImportDraft.model_validate(draft_data),
        issues=(),
        responses=(make_response(Decimal("0.01")),),
    )


def test_entity_counts(import_result: ImportResult) -> None:
    assert entity_counts(import_result.draft.scenario) == {
        "scenes": 2,
        "npcs": 1,
        "items": 0,
        "clues": 1,
        "handouts": 0,
        "encounters": 0,
        "lore": 0,
        "statblocks": 0,
    }


def test_entity_counts_statblocks_is_dict(draft_data: dict[str, Any]) -> None:
    """statblocks — словарь, а не кортеж: счётчик берёт число ключей."""
    draft_data["scenario"]["statblocks"] = {
        "keeper": {"name": "Хранитель", "ac": 13, "hp": 27, "checks": {}},
        "wraith": {"name": "Призрак", "ac": 12, "hp": 10, "checks": {}},
    }
    scenario = ImportDraft.model_validate(draft_data).scenario

    assert entity_counts(scenario)["statblocks"] == 2


def test_report_sections_clean(import_result: ImportResult) -> None:
    report = render_report(import_result, source_name="module.md", language="ru")

    assert report.startswith("# Отчёт импорта: module.md")
    assert "- Исходный файл: module.md" in report
    assert "- Язык: ru" in report
    for section in (
        "## Сущности",
        "## Проблемы валидации",
        "## Заметки модели",
        "## Вызовы модели",
        "## Что дальше",
    ):
        assert section in report
    assert "- scenes: 2" in report
    assert "ссылок-проблем нет" in report
    assert "- Вызовов: 1" in report
    assert "- Стоимость: $0.01" in report
    assert "`module.ru.yaml`" in report
    assert "content/scenarios/module.ru.yaml" in report
    assert "ai-dnd-play seed --scenario module --lang ru" in report
    assert "ai-dnd-play play --scenario module --lang ru --from-db" in report


def test_report_notes_grouped_by_kind(
    draft_data: dict[str, Any], make_response: Callable[[Decimal], LLMResponse]
) -> None:
    draft_data["notes"] = [
        {"kind": "assumption", "path": "scenes.square.checks.look", "text": "DC взят 12."},
        {"kind": "question", "path": "npcs.elder", "text": "Кто такой староста?"},
        {"kind": "missing", "path": "scenes.crypt", "text": "Нет описания усыпальницы."},
    ]
    result = ImportResult(
        draft=ImportDraft.model_validate(draft_data),
        issues=(),
        responses=(make_response(Decimal("0")),),
    )

    report = render_report(result, source_name="module.md", language="ru")

    assert "### Допущение" in report
    assert "### Вопрос" in report
    assert "### Не хватает данных" in report
    assert "- `scenes.square.checks.look` — DC взят 12." in report
    assert "- `npcs.elder` — Кто такой староста?" in report
    assert "- `scenes.crypt` — Нет описания усыпальницы." in report
    # Порядок групп фиксирован: допущения → вопросы → не хватает данных.
    assert report.index("### Допущение") < report.index("### Вопрос")
    assert report.index("### Вопрос") < report.index("### Не хватает данных")


def test_report_without_notes(
    draft_data: dict[str, Any], make_response: Callable[[Decimal], LLMResponse]
) -> None:
    draft_data["notes"] = []
    result = ImportResult(
        draft=ImportDraft.model_validate(draft_data),
        issues=(),
        responses=(make_response(Decimal("0")),),
    )

    assert "заметок нет" in render_report(result, source_name="module.md", language="ru")


def test_report_lists_issues_and_calls(
    import_result: ImportResult, make_response: Callable[[Decimal], LLMResponse]
) -> None:
    issue = ScenarioIssue("unknown_scene", "scenes.square.exits[0]", {"scene": "nowhere"})
    result = ImportResult(
        draft=import_result.draft,
        issues=(issue,),
        responses=(make_response(Decimal("0.01")), make_response(Decimal("0.02"))),
    )

    report = render_report(result, source_name="module.md", language="ru")

    assert str(issue) in report
    assert "ссылок-проблем нет" not in report
    assert "- Вызовов: 2" in report
    assert "- Стоимость: $0.03" in report


def test_write_result_roundtrip(
    tmp_path: Path,
    import_result: ImportResult,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
) -> None:
    """YAML читается обратно той же схемой и проходит проверку ссылок без проблем."""
    out_dir = tmp_path / "nested" / "out"

    yaml_path, report_path = write_result(
        out_dir, "module", import_result, source_name="module.md", language="ru"
    )

    assert yaml_path == out_dir / "module.ru.yaml"
    assert report_path == out_dir / "module.ru.report.md"
    loaded = Scenario.model_validate(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert loaded == import_result.draft.scenario
    assert find_scenario_issues(loaded, (srd_bestiary,), srd5_ruleset) == []
    # allow_unicode: кириллица в файле как есть, не \uXXXX.
    assert "Тестовый импорт" in yaml_path.read_text(encoding="utf-8")
    assert report_path.read_text(encoding="utf-8") == render_report(
        import_result, source_name="module.md", language="ru"
    )


def test_write_result_english_names(
    tmp_path: Path,
    draft_data: dict[str, Any],
    make_response: Callable[[Decimal], LLMResponse],
) -> None:
    draft_data["scenario"]["scenario"]["language"] = "en"
    result = ImportResult(
        draft=ImportDraft.model_validate(draft_data),
        issues=(),
        responses=(make_response(Decimal("0")),),
    )

    yaml_path, report_path = write_result(
        tmp_path, "well", result, source_name="well.docx", language="en"
    )

    assert yaml_path.name == "well.en.yaml"
    assert report_path.name == "well.en.report.md"
    assert "# Отчёт импорта: well.docx" in report_path.read_text(encoding="utf-8")
