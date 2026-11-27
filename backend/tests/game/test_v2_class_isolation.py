"""Юнит-тесты изоляции пакетов классов сценария (подэтап 2b).

Без БД/сети/LLM: реальные load_game_content/load_party/load_class_pack читают
content/ (только чтение). Пакет worms-ship («worm-invasion-act1», три корабельных
класса) изолирован от fantasy-core («five-guardians»: fighter/cleric/rogue/...):
чужой класс отклоняется ValueError, доступные классы — ровно классы пакета.
"""

from pathlib import Path

import pytest

from ai_dnd.game.content import GameContent, load_game_content, load_party
from ai_dnd.game.skills import load_class_pack

FANTASY_PACK = "fantasy-core"
WORMS_PACK = "worms-ship"
WORMS_CLASSES = {"ship_scout", "ship_striker", "ship_tender"}
FANTASY_CLASSES = {"fighter", "cleric", "rogue", "ranger"}

SCENARIO_PACKS = (
    ("five-guardians", "ru", FANTASY_PACK),
    ("shadow-over-millford", "ru", FANTASY_PACK),
    ("shadow-over-millford", "en", FANTASY_PACK),
    ("well-of-forgotten-names", "ru", FANTASY_PACK),
    ("worm-invasion-act1", "ru", WORMS_PACK),
)


@pytest.fixture(scope="session")
def fantasy(content_dir: Path) -> GameContent:
    return load_game_content(content_dir, "five-guardians", "ru")


@pytest.fixture(scope="session")
def worms(content_dir: Path) -> GameContent:
    return load_game_content(content_dir, "worm-invasion-act1", "ru")


# --- Пакеты не пересекаются, сценарий грузит ровно свой пакет ---


def test_packs_disjoint(content_dir: Path) -> None:
    fantasy_classes = set(load_class_pack(content_dir, FANTASY_PACK).classes)
    worms_classes = set(load_class_pack(content_dir, WORMS_PACK).classes)

    assert fantasy_classes >= FANTASY_CLASSES
    assert worms_classes == WORMS_CLASSES
    assert fantasy_classes.isdisjoint(worms_classes)


@pytest.mark.parametrize(("slug", "language", "pack_id"), SCENARIO_PACKS)
def test_scenario_loads_own_pack(content_dir: Path, slug: str, language: str, pack_id: str) -> None:
    content = load_game_content(content_dir, slug, language)

    assert content.scenario.scenario.class_pack == pack_id
    assert set(content.classes) == set(load_class_pack(content_dir, pack_id).classes)


def test_available_classes_match_pack(fantasy: GameContent, worms: GameContent) -> None:
    assert set(fantasy.classes) == FANTASY_CLASSES
    assert set(worms.classes) == WORMS_CLASSES
    assert "ship_scout" not in fantasy.classes
    assert "fighter" not in worms.classes


# --- Свой класс садится за стол, чужой отклоняется ValueError ---


def test_fantasy_party_loads(content_dir: Path, fantasy: GameContent) -> None:
    seats, actors = load_party(content_dir, fantasy, ["fighter", "cleric", "rogue"])

    assert [actor.id for actor in actors] == ["fighter", "cleric", "rogue"]
    assert [seat.player_id for seat in seats] == ["p1", "p2", "p3"]
    for seat in seats:
        assert seat.skills == fantasy.classes[seat.actor_id].skills
    by_actor = {seat.actor_id: seat for seat in seats}
    assert by_actor["cleric"].cure_wounds is not None
    assert by_actor["fighter"].cure_wounds is None


def test_worms_party_loads(content_dir: Path, worms: GameContent) -> None:
    seats, actors = load_party(content_dir, worms, ["ship_scout", "ship_striker", "ship_tender"])

    assert [actor.id for actor in actors] == ["ship_scout", "ship_striker", "ship_tender"]
    assert [seat.actor_id for seat in seats] == ["ship_scout", "ship_striker", "ship_tender"]
    for seat in seats:
        assert seat.skills == worms.classes[seat.actor_id].skills


@pytest.mark.parametrize(
    ("scenario", "alien"),
    [("five-guardians", "ship_scout"), ("worm-invasion-act1", "fighter")],
)
def test_alien_class_rejected(content_dir: Path, scenario: str, alien: str) -> None:
    content = load_game_content(content_dir, scenario, "ru")

    with pytest.raises(ValueError, match="недоступен"):
        load_party(content_dir, content, [alien])


def test_mixed_party_rejected(content_dir: Path, fantasy: GameContent) -> None:
    """Свой класс в партии не легализует чужой: падает вся загрузка."""
    with pytest.raises(ValueError, match="недоступен"):
        load_party(content_dir, fantasy, ["fighter", "ship_scout"])
