"""Сжатие истории кампании (архитектура, раздел 9.3).

Последние ``HISTORY_TURNS`` ходов идут в промпт дословно; более старые сворачиваются в
сводку «что было раньше». Сводка обновляется одним вызовом задачи ``summary`` раз в
``SUMMARY_EVERY`` ходов, а не на каждом ходе. Она сжимает предыдущую сводку вместе с
ходами, ушедшими из дословного окна, поэтому размер промпта не растёт с длиной игры.

Журнал фактов (улики, отношения NPC, обещания, задания) хранится в состоянии игры
отдельно — факты, записанные кодом, надёжнее «памяти» модели.
"""

from collections.abc import Sequence

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import Message

# Раз в сколько ходов обновлять сводку (K). Дословное окно N — HISTORY_TURNS в master.
SUMMARY_EVERY = 10

# Маркер в системном промпте суммаризатора. По нему модель-заглушка отличает задачу
# сводки от рассказа; строка продублирована в fake-провайдере без импорта game (слои).
SUMMARY_MARKER = "You are the session summarizer."

_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


def summary_system_prompt(language: str) -> str:
    name = _LANGUAGE_NAMES.get(language, language)
    return (
        f"{SUMMARY_MARKER} You compress the earlier part of a tabletop RPG session into a "
        f"concise recap written in {name}. Preserve the key facts a game master must not "
        "forget: discovered clues and their meaning, NPC names and relations, promises and "
        "deals, active quests and goals, important items, names, places, decisions and their "
        "outcomes, personal details and backstory players share about their characters "
        "(family, keepsakes, oaths, fears). Drop scenery, repetition and blow-by-blow combat "
        "detail. Fold the existing "
        "recap together with the new turns into one updated recap; do not append a second "
        "recap. Keep it under about 250 words. Output only the recap text, no headings or "
        "preamble. Prompts are in English; write the recap in the game language."
    )


def summary_user_message(previous_summary: str, turns: Sequence[tuple[str, str]]) -> str:
    transcript = "\n\n".join(f"{entry}\nDM: {narration}" for entry, narration in turns)
    existing = previous_summary.strip() or "(none yet)"
    return (
        f"# Existing recap (update and extend it)\n{existing}\n\n"
        f"# New turns to fold in\n{transcript}"
    )


async def build_summary(
    gateway: LLMGateway,
    *,
    language: str,
    previous_summary: str,
    turns: Sequence[tuple[str, str]],
) -> str:
    """Один вызов задачи ``summary``: сжать предыдущую сводку и ушедшие ходы."""
    messages = [
        Message.system(summary_system_prompt(language)),
        Message.user(summary_user_message(previous_summary, turns)),
    ]
    response = await gateway.complete("summary", messages)
    return response.text.strip()
