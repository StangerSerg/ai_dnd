"""События истории — поверх событий движка правил (архитектура, раздел 2.3).

Движок записывает броски, урон и бой. Здесь — то, что происходит в истории: действия
игроков, рассказ ведущего, смена сцены, открытые улики, отношение NPC, найденные
предметы, заметки ведущего. Журнал сессии — общий поток тех и других событий.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_dnd.rules.events import Event as RulesEvent
from ai_dnd.rules.state import Actor
from ai_dnd.rules.visibility import DM, PUBLIC, Visibility

Attitude = Literal["friendly", "neutral", "hostile"]
RevealedKind = Literal["clue", "handout", "item", "lore"]


class GameEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visibility: Visibility = PUBLIC


class PlayerActed(GameEventBase):
    """Что игрок сказал или сделал от лица персонажа."""

    type: Literal["PlayerActed"] = "PlayerActed"
    actor_id: str
    text: str


class Narrated(GameEventBase):
    """Рассказ ведущего игрокам."""

    type: Literal["Narrated"] = "Narrated"
    text: str


class SceneChanged(GameEventBase):
    type: Literal["SceneChanged"] = "SceneChanged"
    scene_id: str
    previous: str | None


class Revealed(GameEventBase):
    """Улика, раздаточный материал, предмет или сведения о мире открыты игрокам."""

    type: Literal["Revealed"] = "Revealed"
    entity_id: str
    kind: RevealedKind


class NpcAttitudeChanged(GameEventBase):
    type: Literal["NpcAttitudeChanged"] = "NpcAttitudeChanged"
    npc_id: str
    attitude: Attitude


class ItemGranted(GameEventBase):
    type: Literal["ItemGranted"] = "ItemGranted"
    actor_id: str
    # Предмет из сценария или придуманный ведущим — тогда id нет.
    item_id: str | None
    name: str


class FactNoted(GameEventBase):
    """Важный факт в журнале ведущего: обещание, договорённость, имя."""

    type: Literal["FactNoted"] = "FactNoted"
    visibility: Visibility = DM
    text: str


class ActorsJoined(GameEventBase):
    """В игру вошли новые участники — например, противники столкновения."""

    type: Literal["ActorsJoined"] = "ActorsJoined"
    actors: tuple[Actor, ...]


GameEvent = Annotated[
    PlayerActed
    | Narrated
    | SceneChanged
    | Revealed
    | NpcAttitudeChanged
    | ItemGranted
    | FactNoted
    | ActorsJoined,
    Field(discriminator="type"),
]

# Запись журнала сессии: событие движка правил или событие истории.
JournalEvent = RulesEvent | GameEvent

JOURNAL_EVENT: TypeAdapter[JournalEvent] = TypeAdapter(JournalEvent)
