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


class NpcMoved(GameEventBase):
    """NPC переместился в другую сцену: авторитетное присутствие (state.npc_locations).

    ``from_scene`` — откуда ушёл (записанная локация или home сценария), если известна.
    """

    type: Literal["NpcMoved"] = "NpcMoved"
    npc_id: str
    scene_id: str
    from_scene: str | None = None


class NpcDied(GameEventBase):
    """NPC погиб — в бою или в рассказе ведущего: авторитетный статус (state.npc_status)."""

    type: Literal["NpcDied"] = "NpcDied"
    npc_id: str


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


class EncounterStarted(GameEventBase):
    """Remember a scenario encounter across combat and scene changes."""

    type: Literal["EncounterStarted"] = "EncounterStarted"
    encounter_id: str


class SummaryUpdated(GameEventBase):
    """Сводка «что было раньше»: сжатие ходов, ушедших из дословного окна (раздел 9.3).

    ``through_turn`` — сколько записей истории уже свёрнуто в сводку; воспроизводимо
    из журнала. Видимость dm: сводка идёт только в промпт ведущего, не игрокам.
    """

    type: Literal["SummaryUpdated"] = "SummaryUpdated"
    visibility: Visibility = DM
    text: str
    through_turn: int


class PartyDefeated(GameEventBase):
    """TPK: все герои без сознания или мертвы — поражение сценария."""

    type: Literal["PartyDefeated"] = "PartyDefeated"
    visibility: Visibility = PUBLIC


GameEvent = Annotated[
    PlayerActed
    | Narrated
    | SceneChanged
    | Revealed
    | NpcAttitudeChanged
    | NpcMoved
    | NpcDied
    | ItemGranted
    | FactNoted
    | ActorsJoined
    | EncounterStarted
    | SummaryUpdated
    | PartyDefeated,
    Field(discriminator="type"),
]

# Запись журнала сессии: событие движка правил или событие истории.
JournalEvent = RulesEvent | GameEvent

JOURNAL_EVENT: TypeAdapter[JournalEvent] = TypeAdapter(JournalEvent)
