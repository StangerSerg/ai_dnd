import pytest
from pydantic import TypeAdapter, ValidationError

from ai_dnd.rules.visibility import DM, PUBLIC, Visibility, can_see, for_player


def test_public_is_seen_by_everyone() -> None:
    assert can_see(PUBLIC)
    assert can_see(PUBLIC, "mira")
    assert can_see(PUBLIC, dm=True)


def test_dm_only_is_hidden_from_players() -> None:
    assert not can_see(DM)
    assert not can_see(DM, "mira")
    assert can_see(DM, dm=True)


def test_private_is_seen_by_that_player_and_the_dm() -> None:
    whisper = for_player("mira")

    assert whisper == "player:mira"
    assert can_see(whisper, "mira")
    assert not can_see(whisper, "brand")
    assert not can_see(whisper)
    assert can_see(whisper, dm=True)


@pytest.mark.parametrize("value", ["public", "dm", "player:mira", "player:seat_12-a"])
def test_valid_visibility(value: str) -> None:
    assert TypeAdapter(Visibility).validate_python(value) == value


@pytest.mark.parametrize("value", ["", "all", "player", "player:", "player:a b", "DM", "dm "])
def test_invalid_visibility(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Visibility).validate_python(value)
