from ai_dnd.i18n import localize


def test_plain_string_is_returned_as_is() -> None:
    assert localize("Сила", "en") == "Сила"


def test_picks_the_requested_language() -> None:
    text = {"ru": "Сила", "en": "Strength"}

    assert localize(text, "ru") == "Сила"
    assert localize(text, "en") == "Strength"


def test_falls_back_to_english_then_to_any_language() -> None:
    assert localize({"ru": "Сила", "en": "Strength"}, "de") == "Strength"
    assert localize({"ru": "Сила"}, "de") == "Сила"
    assert localize({"ru": "Сила", "de": "Stärke"}, "fr", fallback="de") == "Stärke"
