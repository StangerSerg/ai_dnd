"""Тесты промптов импорта: маркер, секции, список библиотечных статблоков, детерминизм."""

from ai_dnd.importer.prompts import IMPORT_MARKER, import_system_prompt, import_user_message
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary

MODULE_TEXT = "# Колодец забытых имён\n\nСавелий ведёт героев к старому колодцу."


def _user_message(ruleset: Ruleset, bestiary: Bestiary, *, language: str = "ru") -> str:
    return import_user_message(
        MODULE_TEXT,
        language=language,
        ruleset=ruleset,
        bestiaries=(bestiary,),
        sheet_template="srd5-basic@1",
    )


def test_system_prompt_marker_and_language() -> None:
    assert IMPORT_MARKER in import_system_prompt("ru")
    assert "in Russian" in import_system_prompt("ru")
    assert "in English" in import_system_prompt("en")
    # Неизвестный язык показывается как есть, без падения.
    assert "in de" in import_system_prompt("de")


def test_user_message_sections(srd5_ruleset: Ruleset, srd_bestiary: Bestiary) -> None:
    message = _user_message(srd5_ruleset, srd_bestiary)

    assert "# Target" in message
    assert "language: ru (Russian)" in message
    assert "ruleset: dnd5e-srd@1" in message
    assert "sheet_template: srd5-basic@1" in message
    assert "# Ruleset ids" in message
    assert "# Library statblocks (reference as '<library>:<id>')" in message
    assert "# Source module text" in message
    # Текст модуля — в конце и целиком, без изменений.
    assert message.endswith(MODULE_TEXT)


def test_ruleset_ids_sorted(srd5_ruleset: Ruleset, srd_bestiary: Bestiary) -> None:
    message = _user_message(srd5_ruleset, srd_bestiary)

    assert f"checks (skills): {', '.join(sorted(srd5_ruleset.checks))}" in message
    assert f"saves: {', '.join(sorted(srd5_ruleset.saves))}" in message
    assert f"damage types: {', '.join(sorted(srd5_ruleset.damage_types))}" in message


def test_library_statblocks_listed(srd5_ruleset: Ruleset, srd_bestiary: Bestiary) -> None:
    """Каждый монстр библиотеки — строкой «- srd:id — Имя (AC …, HP …; attacks: …)»."""
    message = _user_message(srd5_ruleset, srd_bestiary)

    for monster_id in srd_bestiary.monsters:
        assert f"- srd:{monster_id} — " in message
    assert "- srd:skeleton — Скелет (AC " in message
    # Монстры отсортированы по id.
    assert message.index("- srd:bandit") < message.index("- srd:commoner")
    assert message.index("- srd:commoner") < message.index("- srd:skeleton")
    # Атаки отсортированы, с бонусом, кубиками и типом урона.
    skeleton_line = next(
        line for line in message.split("\n") if line.startswith("- srd:skeleton — ")
    )
    assert skeleton_line.index("shortbow +4 (1d6+2 piercing)") < skeleton_line.index(
        "shortsword +4 (1d6+2 piercing)"
    )


def test_library_localized_to_language(srd5_ruleset: Ruleset, srd_bestiary: Bestiary) -> None:
    message = _user_message(srd5_ruleset, srd_bestiary, language="en")

    assert "- srd:skeleton — Skeleton (AC " in message
    assert "Скелет" not in message


def test_empty_bestiary_list(srd5_ruleset: Ruleset) -> None:
    message = import_user_message(
        MODULE_TEXT,
        language="ru",
        ruleset=srd5_ruleset,
        bestiaries=(),
        sheet_template="srd5-basic@1",
    )

    assert "# Library statblocks (reference as '<library>:<id>')" in message
    assert "- srd:" not in message


def test_user_message_deterministic(srd5_ruleset: Ruleset, srd_bestiary: Bestiary) -> None:
    assert _user_message(srd5_ruleset, srd_bestiary) == _user_message(srd5_ruleset, srd_bestiary)
