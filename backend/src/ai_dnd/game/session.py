"""Состояние игровой сессии: правила плюс история.

``SessionSetup`` — то, что задаётся при создании сессии и дальше не меняется: сценарий,
язык, уровень импровизации, кто за каким персонажем. ``SessionState`` — то, что меняется
событиями: состояние движка правил, текущая сцена, открытые игрокам сущности,
отношение NPC, найденные предметы, заметки ведущего.

Состояние неизменяемо и получается проигрыванием журнала: ``apply_events``.
"""

from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.game.events import (
    ActorsJoined,
    Attitude,
    EncounterStarted,
    FactNoted,
    GameEventBase,
    ItemEnhanced,
    ItemEquipped,
    ItemGranted,
    ItemUnequipped,
    ItemUsed,
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
from ai_dnd.rules.engine import ITEM_CONDITION_PREFIX, buff_condition_id, item_condition_id
from ai_dnd.rules.state import ActiveCondition, GameState
from ai_dnd.scenarios.schemas import MAX_ITEM_ENHANCEMENT, ItemSlot

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
    quantity: Annotated[int, Field(ge=1)] = 1
    enhancement: Annotated[int, Field(ge=0, le=MAX_ITEM_ENHANCEMENT)] = 0


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
    # Экипировка: actor_id -> {item_id: slot}. Владение остаётся в inventory; надетый
    # предмет также отражён состоянием «item:<id>:<enh>» у актёра в rules — так
    # модификаторы видит движок, а снятие/расход откатывает их без пересчёта карточки.
    # Слот «other» вмещает несколько предметов, остальные — по одному.
    equipment: dict[str, dict[str, ItemSlot]] = Field(default_factory=dict)
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
                InventoryItem(
                    item_id=event.item_id,
                    name=event.name,
                    quantity=event.quantity,
                    enhancement=event.enhancement,
                ),
            )
            inventory = {**state.inventory, event.actor_id: items}
            return state.model_copy(update={"inventory": inventory})
        case ItemEquipped():
            return _equip(state, event)
        case ItemUnequipped():
            return _unequip(state, event.actor_id, event.item_id)
        case ItemUsed():
            return _consume(state, event)
        case ItemEnhanced():
            return _enhance(state, event)
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


def _inventory_entry(state: SessionState, actor_id: str, item_id: str) -> InventoryItem | None:
    return next(
        (item for item in state.inventory.get(actor_id, ()) if item.item_id == item_id), None
    )


def _with_item_condition(
    rules: GameState, actor_id: str, item_id: str, enhancement: int
) -> GameState:
    """Надетый предмет как состояние актёра: прежнее состояние этого предмета заменяется."""
    actor = rules.actors.get(actor_id)
    if actor is None:
        return rules
    prefix = f"{ITEM_CONDITION_PREFIX}{item_id}:"
    kept = tuple(item for item in actor.conditions if not item.id.startswith(prefix))
    condition = ActiveCondition(id=item_condition_id(item_id, enhancement))
    return rules.with_actor(actor.with_conditions((*kept, condition)))


def _without_item_condition(rules: GameState, actor_id: str, item_id: str) -> GameState:
    actor = rules.actors.get(actor_id)
    if actor is None:
        return rules
    prefix = f"{ITEM_CONDITION_PREFIX}{item_id}:"
    kept = tuple(item for item in actor.conditions if not item.id.startswith(prefix))
    if kept == actor.conditions:
        return rules
    return rules.with_actor(actor.with_conditions(kept))


def _set_worn(
    state: SessionState, actor_id: str, worn: dict[str, ItemSlot]
) -> dict[str, dict[str, ItemSlot]]:
    equipment = {**state.equipment}
    if worn:
        equipment[actor_id] = worn
    else:
        equipment.pop(actor_id, None)
    return equipment


def _equip(state: SessionState, event: ItemEquipped) -> SessionState:
    entry = _inventory_entry(state, event.actor_id, event.item_id)
    if entry is None:
        return state
    worn = {**state.equipment.get(event.actor_id, {})}
    rules = state.rules
    # Слот «other» не конфликтует; прочие слоты — один предмет, прежний снимается.
    if event.slot != "other":
        for other_id, slot in tuple(worn.items()):
            if slot == event.slot and other_id != event.item_id:
                del worn[other_id]
                rules = _without_item_condition(rules, event.actor_id, other_id)
    worn[event.item_id] = event.slot
    rules = _with_item_condition(rules, event.actor_id, event.item_id, entry.enhancement)
    return state.model_copy(
        update={"equipment": _set_worn(state, event.actor_id, worn), "rules": rules}
    )


def _unequip(state: SessionState, actor_id: str, item_id: str) -> SessionState:
    worn = {**state.equipment.get(actor_id, {})}
    if worn.pop(item_id, None) is None:
        return state
    rules = _without_item_condition(state.rules, actor_id, item_id)
    return state.model_copy(update={"equipment": _set_worn(state, actor_id, worn), "rules": rules})


def _consume(state: SessionState, event: ItemUsed) -> SessionState:
    entry = _inventory_entry(state, event.actor_id, event.item_id)
    if entry is None:
        return state
    if entry.quantity <= 1:
        kept = tuple(
            item for item in state.inventory[event.actor_id] if item.item_id != event.item_id
        )
    else:
        kept = tuple(
            item.model_copy(update={"quantity": item.quantity - 1})
            if item.item_id == event.item_id
            else item
            for item in state.inventory[event.actor_id]
        )
    state = state.model_copy(update={"inventory": {**state.inventory, event.actor_id: kept}})
    if entry.quantity <= 1:
        # Последняя доза: предмет исчезает вместе со слотом, если был надет.
        state = _unequip(state, event.actor_id, event.item_id)
    if event.effect == "buff":
        actor = state.rules.actors.get(event.actor_id)
        if actor is not None:
            condition = ActiveCondition(id=buff_condition_id(event.item_id), rounds=event.rounds)
            existing = actor.condition(condition.id)
            # Как в rules.apply: повторный баф не складывается, остаётся более долгий.
            if existing is None or (
                existing.rounds is not None
                and (event.rounds is None or event.rounds > existing.rounds)
            ):
                others = tuple(item for item in actor.conditions if item.id != condition.id)
                state = state.model_copy(
                    update={
                        "rules": state.rules.with_actor(actor.with_conditions((*others, condition)))
                    }
                )
    return state


def _enhance(state: SessionState, event: ItemEnhanced) -> SessionState:
    entry = _inventory_entry(state, event.actor_id, event.item_id)
    if entry is None:
        return state
    items = tuple(
        item.model_copy(update={"enhancement": event.enhancement})
        if item.item_id == event.item_id
        else item
        for item in state.inventory[event.actor_id]
    )
    state = state.model_copy(update={"inventory": {**state.inventory, event.actor_id: items}})
    if event.item_id in state.equipment.get(event.actor_id, {}):
        state = state.model_copy(
            update={
                "rules": _with_item_condition(
                    state.rules, event.actor_id, event.item_id, event.enhancement
                )
            }
        )
    return state
