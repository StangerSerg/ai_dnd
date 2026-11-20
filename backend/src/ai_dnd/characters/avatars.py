"""Пресеты аватаров персонажей: allowlist путей из frontend/public/images/avatars.

Портрет хранится в ``characters.portrait`` как публичный путь к картинке;
произвольные URL запрещены — только пресеты ниже (или None — без аватара).
"""

from fastapi import HTTPException

AVATAR_PRESETS: frozenset[str] = frozenset(
    {
        "/images/avatars/dwarf-cleric.png",
        "/images/avatars/dwarf-warrior.png",
        "/images/avatars/elf-ranger.png",
        "/images/avatars/elf-rogue.png",
        "/images/avatars/human-knight.png",
        "/images/avatars/human-mage.png",
    }
)


def validate_portrait(portrait: str | None) -> str | None:
    """None допустим (без аватара); иначе путь обязан быть пресетом из allowlist."""
    if portrait is not None and portrait not in AVATAR_PRESETS:
        raise HTTPException(422, "invalid_portrait")
    return portrait
