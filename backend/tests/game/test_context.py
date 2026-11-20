"""Постоянные тесты подэтапа 2.3: сборка контекста v2 и кэш начала промпта.

Слои промпта (сцена, видимость public/dm, найденные фрагменты, память), бюджет слоя
фрагментов и байт-в-байт стабильность системного промпта/истории между ходами.
Поиск подменяется стабом: сам RRF/гибридный поиск здесь не тестируется.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from itertools import pairwise

import pytest

from ai_dnd.ai.provider import CompletionRequest, ProviderResult
from ai_dnd.ai.providers.fake import _INDEXED_ACTION, FakeProvider
from ai_dnd.ai.schemas import Message
from ai_dnd.game.content import GameContent
from ai_dnd.game.context import ActionLine, ContextBuilder
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster, PlayerInput, ToolUsed, TurnUpdate
from ai_dnd.game.session import SessionSetup, SessionState
from ai_dnd.rag.search import FRAGMENT_BUDGET_TOKENS, LoreSearcher, SearchHit

FRAG_HEADER = "# Relevant scenario fragments"
PUBLIC_TEXT = "Публичное предание: мельник Тихон пропал в ночь новолуния."
DM_TEXT = "Секрет ведущего: культ Змеи держит пленников в подвале мельницы."
# Оценка бюджета в format_fragments: ~3 символа на токен.
CHARS_PER_TOKEN = 3


class RecordingProvider(FakeProvider):
    """Стаб с записью всех запросов; решения — по обычному сценарию заглушки."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        self.requests.append(request)
        return await super().complete(request)

    @property
    def decisions(self) -> list[CompletionRequest]:
        return [request for request in self.requests if request.json_output]

    @property
    def narrations(self) -> list[CompletionRequest]:
        return [request for request in self.requests if not request.json_output]


class LookupProvider(RecordingProvider):
    """На вход со словом LOOKUP: сначала lookup_lore, затем stay по тем же индексам."""

    def __init__(self) -> None:
        super().__init__()
        self._did_lookup = False

    def _decision_json(self, message: Message) -> str:
        content = message.content or ""
        section = content.split("# Unresolved original inputs (authoritative indices)", 1)[-1]
        section = section.split("\n\n#", 1)[0]
        actions = _INDEXED_ACTION.findall(section)
        if actions and "lookup" in actions[0][2].lower():
            index, actor, _ = actions[0]
            if not self._did_lookup:
                self._did_lookup = True
                return json.dumps(
                    {
                        "actions": [
                            {
                                "input_index": int(index),
                                "actor_id": actor,
                                "kind": "tool",
                                "tool_name": "lookup_lore",
                                "arguments": {"query": "культ Змея"},
                                "continue_after": True,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {"actions": [{"input_index": int(index), "actor_id": actor, "kind": "stay"}]},
                ensure_ascii=False,
            )
        return super()._decision_json(message)


class NoteFactProvider(RecordingProvider):
    """Первое решение — note_fact, дальше stay."""

    def __init__(self, fact: str) -> None:
        super().__init__()
        self._fact = fact
        self._first_decision = True

    def _decision_json(self, message: Message) -> str:
        content = message.content or ""
        section = content.split("# Unresolved original inputs (authoritative indices)", 1)[-1]
        section = section.split("\n\n#", 1)[0]
        actions = _INDEXED_ACTION.findall(section)
        if self._first_decision and actions:
            self._first_decision = False
            index, actor, _ = actions[0]
            return json.dumps(
                {
                    "actions": [
                        {
                            "input_index": int(index),
                            "actor_id": actor,
                            "kind": "tool",
                            "tool_name": "note_fact",
                            "arguments": {"text": self._fact},
                            "continue_after": False,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return super()._decision_json(message)


class StubSearcher(LoreSearcher):
    """Поиск-стаб: фиксированные фрагменты без базы и эмбеддингов."""

    def __init__(self, hits: Sequence[SearchHit]) -> None:
        self._hits = list(hits)
        self.queries: list[str] = []

    async def search(self, query: str, *, player: bool = False, limit: int = 6) -> list[SearchHit]:
        self.queries.append(query)
        return self._hits


def _hit(key: str, text: str, visibility: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
        type="lore",
        key=key,
        scene_id=None,
        visibility=visibility,
        text=text,
        score=1.0,
        similarity=0.9,
    )


@pytest.fixture
def hits() -> list[SearchHit]:
    return [_hit("public-lore", PUBLIC_TEXT, "public"), _hit("dm-lore", DM_TEXT, "dm")]


async def _play(master: GameMaster, text: str) -> list[TurnUpdate]:
    return [
        update async for update in master.play_turn([PlayerInput(actor_id="fighter", text=text)])
    ]


# --- Кэш начала промпта: системный промпт и история байт-стабильны ---


async def test_system_prompt_byte_stable_across_turns(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
    game_content: GameContent,
    setup: SessionSetup,
) -> None:
    provider = RecordingProvider()
    master, _ = make_master(provider)
    async for _ in master.open_session():
        pass
    # Разные ходы, включая проверку навыка, меняющей состояние мира.
    for text in ("ждём", "осматриваю зал таверны", "ждём"):
        await _play(master, text)

    builder = ContextBuilder(game_content, setup)
    decision_systems = {r.messages[0].content for r in provider.decisions}
    narration_systems = {r.messages[0].content for r in provider.narrations}
    assert len(decision_systems) == 1
    assert narration_systems == {builder.system_prompt}
    assert all(
        (r.messages[0].content or "").startswith(builder.decision_prompt)
        for r in provider.decisions
    )
    # Прозаический стиль рендерится только рассказчику: у планировщика секции «# Style» нет.
    assert "# Style" in builder.system_prompt
    assert all("# Style" not in (system or "") for system in decision_systems)


async def test_history_is_append_only_between_turns(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> None:
    provider = RecordingProvider()
    master, _ = make_master(provider)
    async for _ in master.open_session():
        pass
    for _ in range(3):
        await _play(master, "ждём")

    decisions = provider.decisions
    assert len(decisions) == 3
    for earlier, later in pairwise(decisions):
        prefix = earlier.messages[:-1]
        assert later.messages[: len(prefix)] == prefix
        assert len(later.messages) > len(earlier.messages)
    # В истории нет ни фрагментов поиска, ни сводки — только чистые ходы.
    for request in decisions:
        assert all(FRAG_HEADER not in (m.content or "") for m in request.messages[1:-1])


async def test_facts_change_only_the_last_user_message(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> None:
    fact = "Борис служит культу Змеи"
    provider = NoteFactProvider(fact)
    master, _ = make_master(provider)
    await _play(master, "говорю с Борисом")
    await _play(master, "ждём")

    first, second = provider.decisions
    assert first.messages[0] == second.messages[0]
    assert "# Your notes" not in (first.messages[-1].content or "")
    assert f"# Your notes\n- {fact}" in (second.messages[-1].content or "")
    assert master.state.facts == (fact,)


# --- Видимость фрагментов: планировщик public+dm, рассказ только public ---


async def test_fragments_visibility_public_vs_dm(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]], hits: list[SearchHit]
) -> None:
    searcher = StubSearcher(hits)
    provider = RecordingProvider()
    master, _ = make_master(provider, searcher=searcher)
    await _play(master, "Что я знаю о мельнице и культе?")

    assert searcher.queries  # предварительный поиск по тексту действий выполнен
    decision = provider.decisions[-1]
    last_user = decision.messages[-1].content or ""
    assert FRAG_HEADER in last_user
    assert PUBLIC_TEXT in last_user
    assert DM_TEXT in last_user
    assert "[dm]" in last_user

    narration_user = provider.narrations[-1].messages[-1].content or ""
    assert PUBLIC_TEXT in narration_user
    assert DM_TEXT not in narration_user

    # Фрагменты только в последнем user-сообщении: не в системе и не в истории.
    assert all(FRAG_HEADER not in (m.content or "") for m in decision.messages[:-1])
    assert all(FRAG_HEADER not in (m.content or "") for m in provider.narrations[-1].messages[:-1])


async def test_no_searcher_means_no_fragments_layer(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> None:
    provider = RecordingProvider()
    master, _ = make_master(provider)
    await _play(master, "Что я знаю о культе Змея?")

    assert all(FRAG_HEADER not in (r.messages[-1].content or "") for r in provider.decisions)
    assert "lookup_lore" not in (provider.decisions[0].messages[0].content or "")


async def test_lookup_lore_returns_fragments_then_resolves_input(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]], hits: list[SearchHit]
) -> None:
    provider = LookupProvider()
    master, _ = make_master(provider, searcher=StubSearcher(hits))
    updates = await _play(master, "LOOKUP: вспомнить сведения о культе Змея.")

    lookups = [u for u in updates if isinstance(u, ToolUsed) and u.name == "lookup_lore"]
    assert len(lookups) == 1
    assert lookups[0].arguments == {"query": "культ Змея"}
    assert lookups[0].result["fragments"]
    assert not master.has_pending_turn

    # Второй вызов планировщика получил результат lookup в журнале операций хода.
    assert len(provider.decisions) == 2
    second_user = provider.decisions[1].messages[-1].content or ""
    assert "lookup_lore" in second_user
    assert DM_TEXT in second_user
    # Слой фрагментов публичного рассказа — только public. (Результат lookup целиком
    # попадает в ledger рассказа; dm-видимость там держится инструкцией промпта.)
    narration_user = provider.narrations[-1].messages[-1].content or ""
    fragment_layer = narration_user.split(FRAG_HEADER, 1)[1].split("Background knowledge only", 1)[
        0
    ]
    assert PUBLIC_TEXT in fragment_layer
    assert DM_TEXT not in fragment_layer


async def test_fragment_layer_respects_token_budget_and_dedups(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> None:
    base = "Фрагмент предания о старой мельнице и деревне. " * 10  # ~480 символов
    hits = [_hit(f"lore-{i}", f"{base}#{i}", "public") for i in range(40)]
    hits.append(_hit("duplicate", hits[0].text, "public"))
    provider = RecordingProvider()
    master, _ = make_master(provider, searcher=StubSearcher(hits))
    await _play(master, "ждём")

    last_user = provider.decisions[-1].messages[-1].content or ""
    section = last_user.split(FRAG_HEADER, 1)[1].split("Background knowledge only", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("- (")]
    assert 0 < len(lines) < 40
    texts = [line.split(") ", 1)[1] for line in lines]
    assert sum(len(text) for text in texts) <= FRAGMENT_BUDGET_TOKENS * CHARS_PER_TOKEN
    assert texts.count(hits[0].text) == 1  # дубль по тексту отброшен


# --- Сборка сообщения хода: сцена, видимость сущностей, память ---


def test_turn_message_scene_visibility_and_notes(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    builder = ContextBuilder(game_content, setup)
    actions = [ActionLine(actor_id="fighter", name="Бранд", text="осматриваю зал")]

    message = builder.turn_message(initial_state, actions)
    assert "# Current scene: Таверна «Хромой гусь» (id: tavern)" in message
    assert "clue_symbol [hidden]" in message
    assert "Secrets (DM only)" in message
    assert "- Бранд (id: fighter): осматриваю зал" in message
    assert "# Your notes" not in message

    updated = initial_state.model_copy(
        update={
            "revealed": (*initial_state.revealed, "clue_symbol"),
            "facts": ("Борис лжёт о волках",),
        }
    )
    after = builder.turn_message(updated, actions)
    assert "clue_symbol [discovered]" in after
    assert "# Your notes\n- Борис лжёт о волках" in after


def test_summary_entry_is_a_separate_layer(
    game_content: GameContent, setup: SessionSetup, initial_state: SessionState
) -> None:
    builder = ContextBuilder(game_content, setup)
    entry = builder.summary_entry("Сводка ранних ходов")
    assert entry.startswith("# Earlier in this session")
    assert entry.endswith("Сводка ранних ходов")
    # Сводка не подмешивается в сообщение хода — это отдельный слой истории.
    assert "# Earlier in this session" not in builder.turn_message(initial_state, ())
