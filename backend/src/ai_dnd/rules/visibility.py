"""Видимость событий и фактов (архитектура, раздел 2.4).

Три уровня: ``public`` — все за столом, ``dm`` — только ведущий,
``player:<id>`` — конкретный игрок (и ведущий).
"""

from typing import Annotated

from pydantic import StringConstraints

PUBLIC = "public"
DM = "dm"
_PLAYER_PREFIX = "player:"

Visibility = Annotated[str, StringConstraints(pattern=r"^(public|dm|player:[A-Za-z0-9_-]{1,64})$")]


def for_player(player_id: str) -> str:
    """Видимость «только этому игроку»."""
    return f"{_PLAYER_PREFIX}{player_id}"


def can_see(visibility: str, player_id: str | None = None, *, dm: bool = False) -> bool:
    """Видит ли получатель то, что помечено этой видимостью. Ведущий видит всё."""
    if dm or visibility == PUBLIC:
        return True
    return player_id is not None and visibility == for_player(player_id)
