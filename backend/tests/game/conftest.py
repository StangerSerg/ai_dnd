"""Общие фикстуры игровых тестов: контент, конфиг ИИ и сборка ведущего со стаб-провайдером.

Без базы и сети: журнал в памяти, провайдер подменяется целиком, состояние сессии —
объектами. Существующие фикстуры верхнего уровня (``content_dir``) переиспользуются.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from ai_dnd.ai.config import LLMConfig, load_llm_config
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.provider import LLMProvider
from ai_dnd.ai.recorder import MemoryRecorder
from ai_dnd.config import Settings
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import SessionSetup, SessionState
from ai_dnd.rag.search import LoreSearcher
from ai_dnd.rules.rng import SeededRng

SCENARIO = "shadow-over-millford"
PARTY = ("fighter", "rogue", "cleric")


@pytest.fixture(scope="session")
def game_content(content_dir: Path) -> GameContent:
    """Встроенный демо-сценарий «Тень над Мельничьим Бродом» на русском."""
    return load_game_content(content_dir, SCENARIO, "ru")


@pytest.fixture(scope="session")
def llm_config() -> LLMConfig:
    # Без файла .env, чтобы локальные настройки разработчика не влияли на тесты.
    return load_llm_config(Settings(_env_file=None).llm_config)


@pytest.fixture
def setup(content_dir: Path, game_content: GameContent) -> SessionSetup:
    seats, _ = load_party(content_dir, game_content, PARTY)
    return SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))


@pytest.fixture
def initial_state(content_dir: Path, game_content: GameContent) -> SessionState:
    _, actors = load_party(content_dir, game_content, PARTY)
    return new_session_state(game_content, actors)


@pytest.fixture(scope="session")
def make_master(
    content_dir: Path, game_content: GameContent, llm_config: LLMConfig
) -> Callable[..., tuple[GameMaster, MemoryJournal]]:
    """Фабрика: свежий ведущий с данным стаб-провайдером, журналом в памяти и seed 7."""

    def make(
        provider: LLMProvider,
        *,
        searcher: LoreSearcher | None = None,
        state: SessionState | None = None,
    ) -> tuple[GameMaster, MemoryJournal]:
        seats, actors = load_party(content_dir, game_content, PARTY)
        session_setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
        gateway = LLMGateway(
            llm_config, dict.fromkeys(llm_config.providers, provider), MemoryRecorder()
        )
        journal = MemoryJournal()
        master = GameMaster(
            game_content,
            session_setup,
            state if state is not None else new_session_state(game_content, actors),
            gateway,
            journal,
            SeededRng(7),
            searcher,
        )
        return master, journal

    return make
