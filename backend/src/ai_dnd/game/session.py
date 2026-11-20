"""Состояние игровой сессии: правила плюс история.

``SessionSetup`` — то, что задаётся при создании сессии и дальше не меняется: сценарий,
язык, уровень импровизации, кто за каким персонажем. ``SessionState`` — то, что меняется
событиями: состояние движка правил, текущая сцена, открытые игрокам сущности,
отношение NPC, найденные предметы, заметки ведущего.

Состояние неизменяемо и получается проигрыванием журнала: ``apply_events``.
"""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.game.events import (
    ActorsJoined,
    Attitude,
    EncounterStarted,
    FactNoted,
    GameEventBase,
    ItemGranted,
    JournalEvent,
    Narrated,
    NpcAttitudeChanged,
    NpcDied,
    NpcMoved,
    Revealed,
    SceneChanged,
    SummaryUpdated,
)
from ai_dnd.game.spells import CureWoundsCapability
from ai_dnd.rules.apply import apply
from ai_dnd.rules.state import GameState

# Насколько ведущему-ИИ можно отходить от сценария (архитектура, раздел 5.3):
# strict — только то, что есть в текущей сцене; moderate — всё из сценария и мелкие
# придуманные предметы; free — любые сцены сценария и любые придуманные предметы.
Improvisation = Literal["strict", "moderate", "free"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlayerSeat(_Frozen):
    """Место за столом: игрок и его персонаж."""

    player_id: str
    actor_id: str
    # Имя персонажа и коротко, кто он: «Полурослик, Плут 3».
    name: str
    summary: str = ""
    skills: tuple[str, ...] = ()
    cure_wounds: CureWoundsCapability | None = None


class SessionSetup(_Frozen):
    # Сценарий — имя файла без языка: «shadow-over-millford».
    scenario: str
    language: str
    improvisation: Improvisation = "moderate"
    seats: tuple[PlayerSeat, ...]

    def seat(self, actor_id: str) -> PlayerSeat | None:
        return next((seat for seat in self.seats if seat.actor_id == actor_id), None)

    @property
    def party(self) -> tuple[str, ...]:
        return tuple(seat.actor_id for seat in self.seats)


class InventoryItem(_Frozen):
    item_id: str | None
    name: str


class SessionState(_Frozen):
    rules: GameState
    scene_id: str
    visited: tuple[str, ...] = ()
    # Сущности сценария, открытые игрокам: улики, материалы, предметы, сведения о мире.
    revealed: tuple[str, ...] = ()
    # Отношение NPC, если оно изменилось по ходу игры.
    attitudes: dict[str, Attitude] = Field(default_factory=dict)
    # Где сейчас находится NPC (npc_id -> scene_id): поверх home из сценария.
    # Авторитетное присутствие; Redis-кэш локаций — производный и пересобираемый.
    npc_locations: dict[str, str] = Field(default_factory=dict)
    # Статус NPC (npc_id -> alive/dead): журнал NpcDied; отсутствие ключа — жив.
    npc_status: dict[str, Literal["alive", "dead"]] = Field(default_factory=dict)
    inventory: dict[str, tuple[InventoryItem, ...]] = Field(default_factory=dict)
    facts: tuple[str, ...] = ()
    started_encounters: tuple[str, ...] = ()
    # Сводка «что было раньше» и сколько записей истории в неё свёрнуто (раздел 9.3).
    summary: str = ""
    summary_through_turn: int = 0
    # Сколько раз ведущий рассказывал.
    turn: int = 0

    def is_revealed(self, entity_id: str) -> bool:
        return entity_id in self.revealed


def apply_event(state: SessionState, event: JournalEvent) -> SessionState:
    """Состояние после события. Событие движка правил меняет ``rules``."""
    if not isinstance(event, GameEventBase):
        return state.model_copy(update={"rules": apply(state.rules, event)})

    match event:
        case SceneChanged():
            visited = state.visited
            if event.scene_id not in visited:
                visited = (*visited, event.scene_id)
            return state.model_copy(update={"scene_id": event.scene_id, "visited": visited})
        case Revealed():
            if state.is_revealed(event.entity_id):
                return state
            return state.model_copy(update={"revealed": (*state.revealed, event.entity_id)})
        case NpcAttitudeChanged():
            attitudes = {**state.attitudes, event.npc_id: event.attitude}
            return state.model_copy(update={"attitudes": attitudes})
        case NpcMoved():
            locations = {**state.npc_locations, event.npc_id: event.scene_id}
            return state.model_copy(update={"npc_locations": locations})
        case NpcDied():
            status = {**state.npc_status, event.npc_id: "dead"}
            return state.model_copy(update={"npc_status": status})
        case ItemGranted():
            items = (
                *state.inventory.get(event.actor_id, ()),
                InventoryItem(item_id=event.item_id, name=event.name),
            )
            inventory = {**state.inventory, event.actor_id: items}
            return state.model_copy(update={"inventory": inventory})
        case FactNoted():
            return state.model_copy(update={"facts": (*state.facts, event.text)})
        case ActorsJoined():
            rules = state.rules
            for actor in event.actors:
                rules = rules.with_actor(actor)
            return state.model_copy(update={"rules": rules})
        case EncounterStarted():
            if event.encounter_id in state.started_encounters:
                return state
            return state.model_copy(
                update={"started_encounters": (*state.started_encounters, event.encounter_id)}
            )
        case Narrated():
            return state.model_copy(update={"turn": state.turn + 1})
        case SummaryUpdated():
            return state.model_copy(
                update={"summary": event.text, "summary_through_turn": event.through_turn}
            )
        case _:
            return state


def apply_events(state: SessionState, events: Iterable[JournalEvent]) -> SessionState:
    for event in events:
        state = apply_event(state, event)
    return state
