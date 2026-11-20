"""Фикстуры offline-тестов RAG: сценарии из content/ без базы, сети и моделей."""

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture(scope="session")
def scenario_ru(content_dir: Path) -> dict[str, Any]:
    """Ru-версия демо-сценария «Тень над Мельничьим Бродом» как сырой YAML."""
    path = content_dir / "scenarios" / "shadow-over-millford.ru.yaml"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def scenario_en(content_dir: Path) -> dict[str, Any]:
    """En-версия того же сценария: те же id, другие тексты."""
    path = content_dir / "scenarios" / "shadow-over-millford.en.yaml"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data
