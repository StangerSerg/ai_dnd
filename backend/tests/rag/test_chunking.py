"""Нарезка сущностей сценария на фрагменты (подэтап 2.2, offline).

Видимость ставится на уровне текста: read_aloud сцены — public, dm_notes — dm;
у npc описание публичное, personality и секреты — только для ведущего.
"""

from typing import Any

import pytest

from ai_dnd.rag.chunking import MAX_CHUNK_CHARS, Chunk, chunk_entity

# Секции YAML сценария -> тип сущности в индексе.
_SECTIONS = {
    "scenes": "scene",
    "npcs": "npc",
    "items": "item",
    "clues": "clue",
    "handouts": "handout",
    "encounters": "encounter",
    "lore": "lore",
}


def _entity(scenario: dict[str, Any], section: str, entity_id: str) -> dict[str, Any]:
    entities: list[dict[str, Any]] = scenario[section]
    return next(e for e in entities if e["id"] == entity_id)


def _texts(chunks: list[Chunk], visibility: str) -> str:
    return "\n".join(c.text for c in chunks if c.visibility == visibility)


# --- Видимость на уровне текста ---


def test_scene_splits_public_and_dm(scenario_ru: dict[str, Any]) -> None:
    scene = _entity(scenario_ru, "scenes", "tavern")
    chunks = chunk_entity("scene", "tavern", scene, "ru")

    assert {c.visibility for c in chunks} == {"public", "dm"}
    public = _texts(chunks, "public")
    dm = _texts(chunks, "dm")
    assert "Таверна «Хромой гусь»" in public
    assert "очага" in public
    assert "Завязка" in dm
    # Секреты ведущего и описания проверок не попадают в публичный текст.
    assert "Завязка" not in public
    assert "Герой осматривает зал, стойку или посуду." in dm
    assert "Герой осматривает зал" not in public


def test_npc_description_public_secrets_dm(scenario_ru: dict[str, Any]) -> None:
    boris = _entity(scenario_ru, "npcs", "boris")
    chunks = chunk_entity("npc", "boris", boris, "ru")

    public = _texts(chunks, "public")
    dm = _texts(chunks, "dm")
    assert "Борис" in public
    assert "Круглолицый трактирщик" in public
    assert "Суетливый и льстивый" in dm
    assert "Работает на культ" in dm
    assert "носит им еду" in dm
    assert "Работает на культ" not in public
    assert "Суетливый" not in public


def test_npc_without_personality_has_only_public() -> None:
    chunks = chunk_entity("npc", "stranger", {"name": "Незнакомец", "description": "Плащ."}, "ru")

    assert [c.visibility for c in chunks] == ["public"]


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [("ritual_bowl", "public"), ("cellar_key", "dm"), ("serpent_amulet", "dm")],
)
def test_item_visibility(scenario_ru: dict[str, Any], entity_id: str, expected: str) -> None:
    item = _entity(scenario_ru, "items", entity_id)
    chunks = chunk_entity("item", entity_id, item, "ru")

    assert chunks
    assert {c.visibility for c in chunks} == {expected}


def test_clue_defaults_to_dm(scenario_ru: dict[str, Any]) -> None:
    clue = _entity(scenario_ru, "clues", "clue_symbol")
    chunks = chunk_entity("clue", "clue_symbol", clue, "ru")

    assert [c.visibility for c in chunks] == ["dm"]
    assert "змей, кусающий свой хвост" in chunks[0].text


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [("reward_notice", "public"), ("cult_letter", "dm")],
)
def test_handout_visibility(scenario_ru: dict[str, Any], entity_id: str, expected: str) -> None:
    handout = _entity(scenario_ru, "handouts", entity_id)
    chunks = chunk_entity("handout", entity_id, handout, "ru")

    assert chunks
    assert {c.visibility for c in chunks} == {expected}


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [("village", "public"), ("serpent_cult", "dm")],
)
def test_lore_visibility(scenario_ru: dict[str, Any], entity_id: str, expected: str) -> None:
    lore = _entity(scenario_ru, "lore", entity_id)
    chunks = chunk_entity("lore", entity_id, lore, "ru")

    assert chunks
    assert {c.visibility for c in chunks} == {expected}


def test_encounter_is_dm_only_with_creature_names(scenario_ru: dict[str, Any]) -> None:
    ritual = _entity(scenario_ru, "encounters", "ritual")
    chunks = chunk_entity("encounter", "ritual", ritual, "ru")

    assert {c.visibility for c in chunks} == {"dm"}
    text = _texts(chunks, "dm")
    assert "Обряд в подвале" in text
    assert "Жрец Змея" in text
    # Существо без name индексируется по statblock.
    ambush = _entity(scenario_ru, "encounters", "goblin_ambush")
    ambush_text = _texts(chunk_entity("encounter", "goblin_ambush", ambush, "ru"), "dm")
    assert "srd:goblin" in ambush_text


def test_statblock_is_dm_only() -> None:
    chunks = chunk_entity("statblock", "srd:goblin", {"name": "Гоблин", "traits": "Труслив."}, "ru")

    assert [c.visibility for c in chunks] == ["dm"]
    assert "Гоблин" in chunks[0].text
    assert "Труслив." in chunks[0].text


def test_unknown_type_is_dm_only() -> None:
    chunks = chunk_entity("mystery", "x", {"note": "Секретное поле."}, "ru")

    assert [c.visibility for c in chunks] == ["dm"]
    assert "Секретное поле." in chunks[0].text


# --- Идентификаторы и стабильность ---


def test_scene_id_only_for_scenes(scenario_ru: dict[str, Any]) -> None:
    scene = _entity(scenario_ru, "scenes", "tavern")
    for chunk in chunk_entity("scene", "tavern", scene, "ru"):
        assert chunk.scene_id == "tavern"
        assert chunk.type == "scene"
        assert chunk.key == "tavern"

    boris = _entity(scenario_ru, "npcs", "boris")
    for chunk in chunk_entity("npc", "boris", boris, "ru"):
        assert chunk.scene_id is None


def test_ord_sequential_and_deterministic(scenario_ru: dict[str, Any]) -> None:
    scene = _entity(scenario_ru, "scenes", "tavern")
    first = chunk_entity("scene", "tavern", scene, "ru")
    second = chunk_entity("scene", "tavern", scene, "ru")

    assert first == second
    assert [c.ord for c in first] == list(range(len(first)))


def test_language_recorded_on_every_chunk(scenario_ru: dict[str, Any]) -> None:
    scene = _entity(scenario_ru, "scenes", "tavern")
    chunks = chunk_entity("scene", "tavern", scene, "ru")

    assert chunks
    assert all(c.language == "ru" for c in chunks)


# --- Локализованные поля ---


def test_localized_dict_picks_language() -> None:
    data = {
        "name": {"ru": "Борис", "en": "Boris"},
        "description": {"ru": "Трактирщик.", "en": "Innkeeper."},
    }
    ru = chunk_entity("npc", "boris", data, "ru")
    en = chunk_entity("npc", "boris", data, "en")

    assert _texts(ru, "public") == "Борис\nТрактирщик."
    assert _texts(en, "public") == "Boris\nInnkeeper."


def test_localized_dict_falls_back_to_default_language() -> None:
    # Перевод на ru отсутствует: localize берёт запасной en (DEFAULT_LANGUAGE).
    data = {"name": "Stranger", "description": {"en": "Innkeeper."}}
    chunks = chunk_entity("npc", "stranger", data, "ru")

    assert _texts(chunks, "public") == "Stranger\nInnkeeper."


# --- Нарезка длинных текстов ---


def test_long_text_split_into_bounded_chunks() -> None:
    paragraphs = [f"Абзац {i}. " + "подробное описание деревни и её жителей " * 8 for i in range(6)]
    chunks = chunk_entity("lore", "long", {"text": "\n\n".join(paragraphs)}, "ru")

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    joined = "\n".join(c.text for c in chunks)
    for i in range(6):
        assert f"Абзац {i}." in joined
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_sentence_boundaries_preserved() -> None:
    sentences = [f"Событие номер {i} случилось в деревне у реки!" for i in range(40)]
    chunks = chunk_entity("lore", "long", {"text": " ".join(sentences)}, "ru")

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    # Границы «! » и «? » соблюдаются: кусок не обрывается посреди предложения.
    assert all(c.text.endswith("!") for c in chunks)


def test_unbreakable_text_hard_split_by_length() -> None:
    word = "бесконечныйтракт" * 200  # 3200 символов без пробелов и границ предложений
    chunks = chunk_entity("lore", "long", {"text": word}, "ru")

    assert [len(c.text) for c in chunks] == [1200, 1200, 800]
    assert "".join(c.text for c in chunks) == word


def test_empty_and_whitespace_fields_produce_no_chunks() -> None:
    assert chunk_entity("scene", "empty", {}, "ru") == []
    assert chunk_entity("lore", "blank", {"text": "   \n  "}, "ru") == []
    # Пустые dm_notes не создают dm-фрагментов.
    chunks = chunk_entity("scene", "s", {"title": "Сцена", "dm_notes": "  "}, "ru")
    assert [c.visibility for c in chunks] == ["public"]


# --- Сводный прогон по всему контенту ---


def test_all_content_entities_chunked_consistently(scenario_ru: dict[str, Any]) -> None:
    total = 0
    for section, type_ in _SECTIONS.items():
        entities: list[dict[str, Any]] = scenario_ru.get(section, [])
        for entity in entities:
            chunks = chunk_entity(type_, entity["id"], entity, "ru")
            assert chunks, f"{type_}:{entity['id']} без фрагментов"
            assert [c.ord for c in chunks] == list(range(len(chunks)))
            for chunk in chunks:
                assert chunk.visibility in {"public", "dm"}
                assert chunk.language == "ru"
                assert chunk.text.strip()
                assert len(chunk.text) <= MAX_CHUNK_CHARS
                assert chunk.scene_id == (entity["id"] if type_ == "scene" else None)
            total += len(chunks)
    assert total > 0


def test_scene_dm_notes_never_leak_into_public(scenario_ru: dict[str, Any]) -> None:
    scenes: list[dict[str, Any]] = scenario_ru["scenes"]
    for scene in scenes:
        dm_notes = str(scene.get("dm_notes", "")).strip()
        if not dm_notes:
            continue
        chunks = chunk_entity("scene", scene["id"], scene, "ru")
        public = _texts(chunks, "public")
        assert dm_notes[:40] not in public


def test_en_scenario_chunked_in_english(scenario_en: dict[str, Any]) -> None:
    scene = _entity(scenario_en, "scenes", "tavern")
    chunks = chunk_entity("scene", "tavern", scene, "en")

    assert all(c.language == "en" for c in chunks)
    public = _texts(chunks, "public")
    assert "The Lame Goose tavern" in public
    assert "Warm firelight" in public
    assert "очага" not in public
