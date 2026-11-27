"""Юнит-тесты пресетов тона ведущего (подэтап 8b), только чистая offline-логика.

Без БД/сети/LLM: реальные ContextBuilder/DmTools и схемы SessionSetup/
CreateSessionRequest; промпты рендерятся в памяти. Проверяются полнота пресетов,
блок тона только у рассказчика (планировщик tone-free), неизменяемость тона
после создания стола и передача tone=body.tone в create_session (AST-инспекция
исходника, без запуска сервера). Сам стиль наррации LLM — вне offline-покрытия.
"""

import ast
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from ai_dnd.game.content import GameContent, load_game_content, load_party
from ai_dnd.game.context import ContextBuilder
from ai_dnd.game.decision import TurnDecision
from ai_dnd.game.session import SessionSetup
from ai_dnd.game.tones import TONE_INSTRUCTIONS, Tone
from ai_dnd.game.tools import DmTools
from ai_dnd.realtime import schemas as realtime_schemas
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.rng import SeededRng

SCENARIO = "five-guardians"
PRESETS = get_args(Tone)

BACKEND = Path(__file__).resolve().parents[2]
REALTIME_SERVICE = BACKEND / "src" / "ai_dnd" / "realtime" / "service.py"


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    return load_game_content(content_dir, SCENARIO, "ru")


@pytest.fixture(scope="session")
def seats(content_dir: Path, content: GameContent) -> tuple:
    loaded, _ = load_party(content_dir, content, ["fighter"])
    return tuple(loaded)


def setup_for(seats: tuple, tone: str) -> SessionSetup:
    return SessionSetup(scenario=SCENARIO, language="ru", seats=seats, tone=tone)


# --- Пресеты: закрытый список, default пустой, свободный текст отвергается ---


def test_presets_complete() -> None:
    assert len(PRESETS) >= 20
    assert set(PRESETS) == set(TONE_INSTRUCTIONS)
    assert TONE_INSTRUCTIONS["default"] == ""
    for tone in PRESETS:
        if tone != "default":
            assert TONE_INSTRUCTIONS[tone].strip(), tone


def test_setup_tone_validation(seats: tuple) -> None:
    for tone in PRESETS:
        assert setup_for(seats, tone).tone == tone
    assert SessionSetup(scenario=SCENARIO, language="ru", seats=()).tone == "default"
    with pytest.raises(ValidationError):
        setup_for(seats, "pirate_of_the_caribbean")


# --- Промпт рассказчика: блок тона present/absent, различие только в инструкции ---


def test_narrator_prompt_tone_block(content: GameContent, seats: tuple) -> None:
    default = ContextBuilder(content, setup_for(seats, "default")).system_prompt
    humor = ContextBuilder(content, setup_for(seats, "humor")).system_prompt
    noir = ContextBuilder(content, setup_for(seats, "noir")).system_prompt

    assert "Narrative voice:" not in default
    for prompt, tone in ((humor, "humor"), (noir, "noir")):
        assert "Narrative voice:" in prompt, tone
        assert TONE_INSTRUCTIONS[tone] in prompt, tone
        assert "preserve the established facts, rules and player agency" in prompt
        assert prompt != default
    assert humor != noir
    # Блок тона — единственное отличие: замена инструкции превращает один промпт в другой.
    assert humor.replace(TONE_INSTRUCTIONS["humor"], TONE_INSTRUCTIONS["noir"]) == noir


def test_planner_prompt_tone_free(content: GameContent, seats: tuple) -> None:
    prompts = {
        tone: ContextBuilder(content, setup_for(seats, tone)).decision_prompt
        for tone in ("default", "humor", "noir")
    }
    # Планировщик отвечает только JSON: прозаический стиль в него не включается.
    assert prompts["default"] == prompts["humor"] == prompts["noir"]
    assert TONE_INSTRUCTIONS["humor"] not in prompts["humor"]
    assert "Narrative voice:" not in prompts["humor"]


# --- Тон неизменяем после создания: ни инструмента, ни экшен-схемы ---


def test_setup_tone_frozen(seats: tuple) -> None:
    frozen = setup_for(seats, "noir")
    with pytest.raises(ValidationError):
        frozen.tone = "humor"
    roundtrip = SessionSetup.model_validate(frozen.model_dump(mode="json"))
    assert roundtrip.tone == "noir"


def test_no_tone_tool_or_action(content: GameContent, seats: tuple) -> None:
    tools = DmTools(content, setup_for(seats, "noir"), RulesEngine(content.ruleset), SeededRng(1))
    names = [spec.name for spec in tools.specs()]
    assert names
    assert not any("tone" in name or "style" in name for name in names)
    assert "tone" not in TurnDecision.model_fields


def test_tone_only_in_create_request() -> None:
    with_tone = [
        obj.__name__
        for obj in vars(realtime_schemas).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and "tone" in obj.model_fields
    ]
    assert with_tone == ["CreateSessionRequest"]
    assert realtime_schemas.CreateSessionRequest.model_fields["tone"].default == "default"


def test_create_session_passes_tone_to_setup() -> None:
    """AST: каждый SessionSetup в create_session получает tone=body.tone (без запуска сервера)."""
    tree = ast.parse(REALTIME_SERVICE.read_text(encoding="utf-8"))
    create = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_session"
    )
    calls = [
        node
        for node in ast.walk(create)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SessionSetup"
    ]
    assert len(calls) >= 2  # пустой setup и setup с местом создателя
    for call in calls:
        tone_kw = next((kw for kw in call.keywords if kw.arg == "tone"), None)
        assert tone_kw is not None, ast.dump(call)
        value = tone_kw.value
        assert isinstance(value, ast.Attribute)
        assert value.attr == "tone"
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "body"
