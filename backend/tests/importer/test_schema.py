"""Тесты схемы ImportDraft: полный черновик со статблоком и encounter, строгость полей."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ai_dnd.importer.schema import ImportDraft
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary
from ai_dnd.scenarios.service import find_scenario_issues


def test_full_draft_with_statblock_and_encounter(
    draft_data: dict[str, Any], srd5_ruleset: Ruleset, srd_bestiary: Bestiary
) -> None:
    """Кастомный статблок и encounter проходят схему и проверку ссылок."""
    draft_data["scenario"]["statblocks"] = {
        "keeper": {
            "name": "Хранитель",
            "ac": 13,
            "hp": 27,
            "hit_dice": "6d8",
            "checks": {"perception": 4},
            "attacks": {
                "touch": {
                    "name": "Касание",
                    "bonus": 5,
                    "damage": "2d6",
                    "damage_type": "necrotic",
                }
            },
            "resistances": ["necrotic"],
        }
    }
    draft_data["scenario"]["encounters"] = [
        {
            "id": "restless",
            "title": "Скелеты",
            "creatures": [{"statblock": "srd:skeleton", "count": 2}],
        }
    ]
    draft_data["scenario"]["scenes"][1]["encounters"] = ["restless"]

    draft = ImportDraft.model_validate_json(json.dumps(draft_data, ensure_ascii=False))

    assert find_scenario_issues(draft.scenario, (srd_bestiary,), srd5_ruleset) == []
    assert draft.scenario.statblocks["keeper"].hp == 27


def test_draft_forbids_extra_keys(draft_data: dict[str, Any]) -> None:
    draft_data["extra"] = "лишнее"

    with pytest.raises(ValidationError, match="extra"):
        ImportDraft.model_validate(draft_data)


def test_note_kind_must_be_known(draft_data: dict[str, Any]) -> None:
    draft_data["notes"][0]["kind"] = "guess"

    with pytest.raises(ValidationError, match="kind"):
        ImportDraft.model_validate(draft_data)


def test_draft_is_frozen(draft_data: dict[str, Any]) -> None:
    draft = ImportDraft.model_validate(draft_data)

    with pytest.raises(ValidationError, match="frozen"):
        draft.notes = ()  # type: ignore[misc]  # намеренно: проверяем runtime-заморозку
