"""Тесты консольной команды import (CliRunner, --fake-llm): без сети, денег и базы."""

from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from docx import Document
from typer.testing import CliRunner, Result

from ai_dnd import console
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import LLMResponse
from ai_dnd.config import Settings
from ai_dnd.importer.pipeline import ImportResult
from ai_dnd.importer.schema import ImportDraft
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary, Scenario
from ai_dnd.scenarios.service import ScenarioIssue, find_scenario_issues

MODULE_TEXT = "# Колодец забытых имён\n\nСавелий ведёт героев к старому колодцу.\n"


@pytest.fixture
def run_cli(
    monkeypatch: pytest.MonkeyPatch, content_dir: Path
) -> Callable[[Sequence[str]], Result]:
    """CliRunner с настройками без .env и широкой консолью (rich не переносит строки)."""
    monkeypatch.setattr(
        console, "get_settings", lambda: Settings(_env_file=None, content_dir=content_dir)
    )
    monkeypatch.setenv("COLUMNS", "400")
    runner = CliRunner()

    def run(args: Sequence[str]) -> Result:
        return runner.invoke(console.app, list(args))

    return run


@pytest.fixture
def module_md(tmp_path: Path) -> Path:
    path = tmp_path / "module.md"
    path.write_text(MODULE_TEXT, encoding="utf-8")
    return path


def _combined(result: Result) -> str:
    return result.output + result.stderr


def test_import_md(
    run_cli: Callable[[Sequence[str]], Result],
    module_md: Path,
    tmp_path: Path,
    srd5_ruleset: Ruleset,
    srd_bestiary: Bestiary,
) -> None:
    out = tmp_path / "out_md"

    result = run_cli(["import", str(module_md), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    yaml_path = out / "module.ru.yaml"
    report_path = out / "module.ru.report.md"
    assert yaml_path.is_file()
    assert report_path.is_file()
    scenario = Scenario.model_validate(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert find_scenario_issues(scenario, (srd_bestiary,), srd5_ruleset) == []
    report = report_path.read_text(encoding="utf-8")
    assert "## Заметки модели" in report
    assert "## Проблемы валидации" in report
    assert "seed" in report
    assert "Проблемы валидации: 0" in result.output
    assert "Заметки модели: 1" in result.output
    assert "Вызовы модели: 1" in result.output


def test_import_docx(run_cli: Callable[[Sequence[str]], Result], tmp_path: Path) -> None:
    doc = Document()
    doc.add_paragraph("Колодец забытых имён", style="Heading 1")
    doc.add_paragraph("Савелий ведёт героев к старому колодцу.")
    docx_path = tmp_path / "module.docx"
    doc.save(str(docx_path))
    out = tmp_path / "out_docx"

    result = run_cli(["import", str(docx_path), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    assert (out / "module.ru.yaml").is_file()
    assert (out / "module.ru.report.md").is_file()


def test_import_pdf(
    run_cli: Callable[[Sequence[str]], Result],
    tmp_path: Path,
    make_pdf: Callable[[Path, Sequence[str]], None],
) -> None:
    pdf_path = tmp_path / "module.pdf"
    make_pdf(pdf_path, ["Well of forgotten names", "Second page"])
    out = tmp_path / "out_pdf"

    result = run_cli(["import", str(pdf_path), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    assert (out / "module.ru.yaml").is_file()


def test_import_english(
    run_cli: Callable[[Sequence[str]], Result], module_md: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out_en"

    result = run_cli(["import", str(module_md), "--lang", "en", "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    yaml_path = out / "module.en.yaml"
    scenario = Scenario.model_validate(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert scenario.scenario.language == "en"


def test_import_unsupported_format_exit1(
    run_cli: Callable[[Sequence[str]], Result], tmp_path: Path
) -> None:
    bad = tmp_path / "module.rtf"
    bad.write_text("{\\rtf1 текст}", encoding="utf-8")

    result = run_cli(["import", str(bad), "--fake-llm", "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "unsupported_format" in _combined(result)
    assert "Traceback" not in _combined(result)
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_import_empty_file_exit1(
    run_cli: Callable[[Sequence[str]], Result], tmp_path: Path
) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")

    result = run_cli(["import", str(empty), "--fake-llm", "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "empty_document" in _combined(result)
    assert "Traceback" not in _combined(result)


def test_import_broken_docx_exit1(
    run_cli: Callable[[Sequence[str]], Result], tmp_path: Path
) -> None:
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"garbage, not a zip package\x00\x01")

    result = run_cli(["import", str(broken), "--fake-llm", "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "invalid_document" in _combined(result)
    assert "Traceback" not in _combined(result)


def test_output_exists_guard_before_read_and_model(
    run_cli: Callable[[Sequence[str]], Result], tmp_path: Path
) -> None:
    """Guard срабатывает до чтения файла и вызова модели: вход даже не существует."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "missing.ru.yaml").write_text("правленый автором черновик", encoding="utf-8")

    result = run_cli(["import", str(tmp_path / "missing.md"), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 1
    combined = _combined(result)
    assert "output_exists" in combined
    assert "FileNotFoundError" not in combined
    assert "Вызовы модели" not in combined
    assert "Traceback" not in combined


def test_force_overwrites_only_with_flag(
    run_cli: Callable[[Sequence[str]], Result], module_md: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    first = run_cli(["import", str(module_md), "--fake-llm", "--out", str(out)])
    assert first.exit_code == 0, _combined(first)
    yaml_path = out / "module.ru.yaml"
    yaml_path.write_text("правка автора", encoding="utf-8")

    blocked = run_cli(["import", str(module_md), "--fake-llm", "--out", str(out)])

    assert blocked.exit_code == 1
    assert "output_exists" in _combined(blocked)
    assert yaml_path.read_text(encoding="utf-8") == "правка автора"

    forced = run_cli(["import", str(module_md), "--fake-llm", "--out", str(out), "--force"])

    assert forced.exit_code == 0, _combined(forced)
    assert yaml_path.read_text(encoding="utf-8") != "правка автора"


def test_markup_in_filename_escaped(
    run_cli: Callable[[Sequence[str]], Result], tmp_path: Path
) -> None:
    """Имя с rich-разметкой «[v2]» не ломает вывод и попадает в имя YAML как есть."""
    marked = tmp_path / "Модуль [v2].md"
    marked.write_text(MODULE_TEXT, encoding="utf-8")
    out = tmp_path / "out_markup"

    result = run_cli(["import", str(marked), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    assert "MarkupError" not in _combined(result)
    assert "[v2]" in result.output
    assert (out / "Модуль [v2].ru.yaml").is_file()


def test_validation_issues_warn_but_exit0(
    run_cli: Callable[[Sequence[str]], Result],
    module_md: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    draft_data: dict[str, Any],
    make_response: Callable[[Decimal], LLMResponse],
) -> None:
    """Проблемы валидации — предупреждение, не ошибка запуска: автор правит YAML."""
    issue = ScenarioIssue("unknown_scene", "scenes.square.exits[0]", {"scene": "nowhere"})
    stub_result = ImportResult(
        draft=ImportDraft.model_validate(draft_data),
        issues=(issue,),
        responses=(make_response(Decimal("0")),),
    )

    async def stub_import(
        gateway: LLMGateway,
        *,
        text: str,
        language: str,
        ruleset: Ruleset,
        bestiaries: Iterable[Bestiary],
        sheet_template: str,
    ) -> ImportResult:
        return stub_result

    monkeypatch.setattr(console, "import_scenario", stub_import)
    out = tmp_path / "out_issues"

    result = run_cli(["import", str(module_md), "--fake-llm", "--out", str(out)])

    assert result.exit_code == 0, _combined(result)
    assert "Проблемы валидации: 1" in result.output
    assert "исправьте YAML перед публикацией" in result.output
    assert (out / "module.ru.yaml").is_file()
    assert str(issue) in (out / "module.ru.report.md").read_text(encoding="utf-8")


def test_bad_language_rejected(
    run_cli: Callable[[Sequence[str]], Result], module_md: Path, tmp_path: Path
) -> None:
    result = run_cli(
        ["import", str(module_md), "--lang", "de", "--fake-llm", "--out", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert "lang" in _combined(result)
