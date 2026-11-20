"""Постоянные тесты подэтапа 2.4 «Память кампании» (архитектура, раздел 9.3).

Дословное окно последних N=12 ходов, rolling-сводка раз в K=10 ходов отдельным вызовом
задачи ``summary``, журнал фактов, событие ``SummaryUpdated`` и ограниченность промпта
на длинной игре (130 ходов со стаб-провайдером). Без сети, базы и платных вызовов.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise

import pytest
from pydantic import TypeAdapter

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.provider import CompletionRequest, ProviderResult
from ai_dnd.ai.providers.fake import _INDEXED_ACTION, FakeProvider
from ai_dnd.ai.schemas import Message
from ai_dnd.game.events import FactNoted, JournalEvent, Narrated, SummaryUpdated
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import HISTORY_TURNS, GameMaster, PlayerInput
from ai_dnd.game.memory import (
    SUMMARY_EVERY,
    SUMMARY_MARKER,
    build_summary,
    summary_system_prompt,
    summary_user_message,
)
from ai_dnd.game.session import SessionState, apply_events
from ai_dnd.rules.visibility import DM

PASSWORD = "MILLFORD-7"
EARLY_FACT = f"Пароль от мельницы: {PASSWORD}"
SUMMARY_HEADER = "# Earlier in this session"
LONG_TURNS = 130

EVENTS = TypeAdapter(list[JournalEvent])


class MemoryProvider(FakeProvider):
    """Ход 1 — note_fact раннего факта, дальше stay; сводка сохраняет пароли из ходов."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CompletionRequest] = []
        self.summary_calls: list[str] = []
        self._first_decision = True

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        self.requests.append(request)
        system = request.messages[0].content or ""
        if SUMMARY_MARKER in system:
            user = request.messages[-1].content or ""
            self.summary_calls.append(user)
            return self._summary_result(request, user)
        return await super().complete(request)

    def _summary_result(self, request: CompletionRequest, user: str) -> ProviderResult:
        previous = user.split("# Existing recap (update and extend it)\n", 1)[-1]
        previous = previous.split("\n\n# New turns", 1)[0].strip()
        previous = "" if previous == "(none yet)" else previous
        lines = [f"Key fact: {fact}" for fact in sorted(set(re.findall(r"MILLFORD-\d+", user)))]
        for kept in previous.splitlines():
            if kept.startswith("Key fact:") and kept not in lines:
                lines.append(kept)
        lines.append(f"Recap of earlier turns (update {len(self.summary_calls)}).")
        return self._result(request, "\n".join(lines), ())

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
                            "arguments": {"text": EARLY_FACT},
                            "continue_after": False,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "actions": [
                    {"input_index": int(index), "actor_id": actor, "kind": "stay"}
                    for index, actor, _ in actions
                ]
            },
            ensure_ascii=False,
        )


class CannedProvider(FakeProvider):
    """Один фиксированный ответ на любой запрос; записывает все запросы."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._canned_text = text
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> ProviderResult:
        self.requests.append(request)
        return self._result(request, self._canned_text, ())


async def _play(master: GameMaster, text: str) -> None:
    async for _ in master.play_turn([PlayerInput(actor_id="fighter", text=text)]):
        pass


@dataclass
class LongSession:
    master: GameMaster
    journal: MemoryJournal
    provider: MemoryProvider
    initial: SessionState

    @property
    def decisions(self) -> list[CompletionRequest]:
        return [request for request in self.provider.requests if request.json_output]


@pytest.fixture(scope="module")
def long_session(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> LongSession:
    """130 ходов со стабом: один прогон на модуль, дальше только проверки состояния."""
    provider = MemoryProvider()
    master, journal = make_master(provider)
    initial = master.state

    async def run() -> None:
        async for _ in master.open_session():
            pass
        await _play(master, f"Запоминаю пароль {PASSWORD} от мельницы и жду.")
        for _ in range(LONG_TURNS - 1):
            await _play(master, "ждём")

    asyncio.run(run())
    return LongSession(master=master, journal=journal, provider=provider, initial=initial)


# --- Короткая сессия: сводка ещё не нужна ---


async def test_no_summary_in_short_session(
    make_master: Callable[..., tuple[GameMaster, MemoryJournal]],
) -> None:
    provider = MemoryProvider()
    master, journal = make_master(provider)
    async for _ in master.open_session():
        pass
    for _ in range(5):
        await _play(master, "ждём")

    assert master.state.summary == ""
    assert master.state.summary_through_turn == 0
    assert provider.summary_calls == []
    assert not any(isinstance(event, SummaryUpdated) for event in journal.events)
    # Факт первого хода уже в журнале фактов, а сводки в промпте нет.
    assert EARLY_FACT in master.state.facts
    assert all(
        SUMMARY_HEADER not in (request.messages[1].content or "")
        for request in provider.requests
        if request.json_output and len(request.messages) > 1
    )


# --- Длинная сессия: окно, сводка, событие, факты, рост промпта ---


def test_summary_triggered_once_per_k_turns(long_session: LongSession) -> None:
    state = long_session.master.state
    # Каждая запись истории — один Narrated; окно N, сводка раз в K ушедших ходов.
    expected_calls = (state.turn - HISTORY_TURNS) // SUMMARY_EVERY
    assert state.turn == LONG_TURNS + 1
    assert state.summary
    assert len(long_session.provider.summary_calls) == expected_calls
    assert state.summary_through_turn == expected_calls * SUMMARY_EVERY


def test_summary_event_journaled_and_monotonic(long_session: LongSession) -> None:
    events = [e for e in long_session.journal.events if isinstance(e, SummaryUpdated)]
    assert events
    throughs = [event.through_turn for event in events]
    assert all(later > earlier for earlier, later in pairwise(throughs))
    assert throughs[-1] <= long_session.master.state.turn - HISTORY_TURNS
    assert all(event.visibility == DM for event in events)
    state = long_session.master.state
    assert events[-1].text == state.summary
    assert events[-1].through_turn == state.summary_through_turn


def test_early_fact_survives_in_summary_and_facts(long_session: LongSession) -> None:
    state = long_session.master.state
    assert PASSWORD in state.summary
    assert EARLY_FACT in state.facts
    noted = [e for e in long_session.journal.events if isinstance(e, FactNoted)]
    assert [event.text for event in noted] == [EARLY_FACT]
    assert noted[0].visibility == DM


def test_summary_is_layer4_between_system_and_history(long_session: LongSession) -> None:
    late = long_session.decisions[-1]
    assert len(late.messages) > 2
    assert late.messages[1].role == "user"
    summary_message = late.messages[1].content or ""
    assert summary_message.startswith(SUMMARY_HEADER)
    assert summary_message.endswith(long_session.master.state.summary)
    assert SUMMARY_HEADER not in (late.messages[0].content or "")
    assert SUMMARY_HEADER not in (late.messages[-1].content or "")


def test_verbatim_window_bounded_and_recent(long_session: LongSession) -> None:
    for request in long_session.decisions:
        assistants = sum(1 for message in request.messages if message.role == "assistant")
        assert assistants <= HISTORY_TURNS

    late = long_session.decisions[-1]
    # system, сводка, N пар «ход/рассказ», последний user.
    history = late.messages[2:-1]
    assert len(history) == 2 * HISTORY_TURNS
    assert [message.role for message in history] == ["user", "assistant"] * HISTORY_TURNS
    # Ранний ход с паролем ушёл из дословного окна (он живёт в сводке и фактах).
    assert PASSWORD not in "".join(message.content or "" for message in history)


def test_prompt_does_not_grow_over_130_turns(long_session: LongSession) -> None:
    decisions = long_session.decisions
    early_chars = sum(len(m.content or "") for m in decisions[39].messages)
    late_chars = sum(len(m.content or "") for m in decisions[-1].messages)
    assert late_chars < early_chars * 1.3
    # И ни один промежуточный ход не раздувался выше границы окна.
    assert all(
        sum(len(m.content or "") for m in request.messages) < early_chars * 1.3
        for request in decisions[39:]
    )


def test_summary_reuses_previous_and_stays_bounded(long_session: LongSession) -> None:
    calls = long_session.provider.summary_calls
    assert len(calls) >= 2
    assert "(none yet)" in calls[0]
    assert PASSWORD in calls[0]  # ранний ход попал в первый вызов суммаризатора
    assert "Key fact:" in calls[1].split("# New turns")[0]
    sizes = [len(call.split("# New turns")[0]) for call in calls]
    assert max(sizes) < 2000  # вход сводки не растёт с числом обновлений


def test_summary_uses_separate_summary_task(
    long_session: LongSession, llm_config: LLMConfig
) -> None:
    summary_requests = [
        request
        for request in long_session.provider.requests
        if SUMMARY_MARKER in (request.messages[0].content or "")
    ]
    assert len(summary_requests) == len(long_session.provider.summary_calls)
    assert all(not request.json_output for request in summary_requests)
    assert {request.model for request in summary_requests} == {llm_config.tasks["summary"].model}
    assert {request.model for request in long_session.decisions} == {
        llm_config.tasks["dm_tools"].model
    }
    assert all("written in Russian" in (r.messages[0].content or "") for r in summary_requests)


def test_system_prompt_byte_stable_over_long_session(long_session: LongSession) -> None:
    game_requests = [
        request
        for request in long_session.provider.requests
        if SUMMARY_MARKER not in (request.messages[0].content or "")
    ]
    decision_systems = {r.messages[0].content for r in game_requests if r.json_output}
    narration_systems = {r.messages[0].content for r in game_requests if not r.json_output}
    assert len(decision_systems) == 1
    assert len(narration_systems) == 1


def test_replay_of_journal_equals_snapshot(long_session: LongSession) -> None:
    state = long_session.master.state
    restored = apply_events(long_session.initial, long_session.journal.events)
    assert restored == state
    assert long_session.journal.state == state

    # События и снимок переживают JSON-сериализацию без потерь.
    events = EVENTS.validate_json(EVENTS.dump_json(long_session.journal.events))
    assert events == long_session.journal.events
    assert apply_events(long_session.initial, events) == state
    assert SessionState.model_validate_json(state.model_dump_json()) == state


# --- Модуль memory.py: один вызов задачи summary ---


def test_summary_user_message_folds_previous_recap() -> None:
    text = summary_user_message(
        "старая сводка", [("Players' actions:\n- Бранд: ждём", "Рассказ ведущего")]
    )
    assert text.startswith("# Existing recap (update and extend it)\nстарая сводка")
    assert "# New turns to fold in\nPlayers' actions:\n- Бранд: ждём\nDM: Рассказ ведущего" in text


def test_summary_user_message_without_previous() -> None:
    assert "(none yet)" in summary_user_message("   ", [])


def test_summary_system_prompt_language() -> None:
    assert summary_system_prompt("ru").startswith(SUMMARY_MARKER)
    assert "written in Russian" in summary_system_prompt("ru")
    assert "written in English" in summary_system_prompt("en")


def test_summary_system_prompt_keeps_personal_details() -> None:
    # Личные детали персонажей (пример live-пробы: кольцо бабушки) должны переживать сводку.
    for language in ("ru", "en"):
        prompt = summary_system_prompt(language).lower()
        assert "personal details" in prompt
        assert "backstory" in prompt
        assert "characters" in prompt
        assert any(word in prompt for word in ("family", "keepsakes", "oaths", "fears"))


def test_summary_system_prompt_marker_and_language_intact() -> None:
    # Добавление перечня фактов не тронуло маркер и остальной промпт.
    for language, name in (("ru", "Russian"), ("en", "English")):
        prompt = summary_system_prompt(language)
        assert prompt.startswith(SUMMARY_MARKER)
        assert f"written in {name}" in prompt
        assert "Keep it under about 250 words" in prompt
        assert "write the recap in the game language" in prompt
        # Инструкция сохранять детали стоит в перечне фактов, до инструкции что отбрасывать.
        assert prompt.index("personal details") < prompt.index("Drop scenery")


async def test_build_summary_single_stripped_call(llm_config: LLMConfig) -> None:
    provider = CannedProvider("  Сводка: герои ждут у мельницы.  ")
    gateway = LLMGateway(llm_config, dict.fromkeys(llm_config.providers, provider))

    result = await build_summary(
        gateway, language="ru", previous_summary="было раньше", turns=[("ход", "рассказ")]
    )

    assert result == "Сводка: герои ждут у мельницы."
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.model == llm_config.tasks["summary"].model
    assert not request.json_output
    assert request.messages[0].role == "system"
    assert SUMMARY_MARKER in (request.messages[0].content or "")
    assert "было раньше" in (request.messages[-1].content or "")


# --- События памяти в журнале и состоянии ---


def test_fact_and_summary_events_apply_to_state(initial_state: SessionState) -> None:
    state = apply_events(
        initial_state,
        [
            FactNoted(text="Обещали защиту деревне"),
            SummaryUpdated(text="Сводка ранних ходов", through_turn=10),
            Narrated(text="Рассказ"),
        ],
    )
    assert state.facts == ("Обещали защиту деревне",)
    assert (state.summary, state.summary_through_turn) == ("Сводка ранних ходов", 10)
    assert state.turn == initial_state.turn + 1
    assert FactNoted(text="x").visibility == DM
    assert SummaryUpdated(text="x", through_turn=1).visibility == DM


def test_memory_events_serialize(initial_state: SessionState) -> None:
    events: list[JournalEvent] = [
        FactNoted(text="факт"),
        SummaryUpdated(text="сводка", through_turn=10),
    ]
    restored = EVENTS.validate_json(EVENTS.dump_json(events))
    assert restored == events
    state = apply_events(initial_state, restored)
    assert SessionState.model_validate_json(state.model_dump_json()) == state
