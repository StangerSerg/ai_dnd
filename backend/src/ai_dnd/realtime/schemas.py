"""JSON-схемы API игровых сессий (подэтап 3.3)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.game.session import Improvisation
from ai_dnd.game.spells import CureWoundsCapability
from ai_dnd.game.tones import Tone
from ai_dnd.scenarios.schemas import Item


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(_Schema):
    scenario_version_id: uuid.UUID
    language: str = "ru"
    dm_mode: Literal["ai", "human"] = "ai"
    improvisation: Improvisation = "moderate"
    tone: Tone = "default"
    # Персонаж создателя: обязателен при dm_mode="ai" (создатель садится игроком).
    character_id: uuid.UUID | None = None
    # Публичность и ёмкость можно задать сразу, не открывая набор отдельным запросом.
    recruiting: bool = False
    seats_total: int | None = Field(default=None, ge=1, le=12)


class CreateSessionResponse(_Schema):
    id: uuid.UUID
    invite_token: str
    recruiting: bool
    seats_total: int | None = None
    # Роль создателя за столом: "player" (ai) или "dm" (human).
    role: str


class JoinRequest(_Schema):
    invite_token: str = Field(min_length=1, max_length=64)
    character_id: uuid.UUID


class SeatInfo(_Schema):
    id: uuid.UUID
    role: str
    controller: str
    user_id: uuid.UUID | None
    character_id: uuid.UUID | None
    actor_id: str
    name: str | None = None
    muted: bool = False
    mute_source: str | None = None
    dice_style: str = "amber"
    # Витрина скиллов класса: id из content/skills.yaml и параметры лечения.
    skills: list[str] = Field(default_factory=list)
    cure_wounds: CureWoundsCapability | None = None


class MuteRequest(_Schema):
    seat_id: uuid.UUID
    muted: bool


class MuteResponse(_Schema):
    seat_id: uuid.UUID
    muted: bool


class KickRequest(_Schema):
    seat_id: uuid.UUID


class KickResponse(_Schema):
    seat_id: uuid.UUID
    removed: bool = True


class TransferHostRequest(_Schema):
    """Передача ведения стола: место игрока, который становится ведущим."""

    seat_id: uuid.UUID


class TransferHostResponse(_Schema):
    # dm_seat_id/removed_seat_id — None в ai-режиме: dm-места нет, цель сохраняет своё место.
    dm_seat_id: uuid.UUID | None = None
    new_host_user_id: uuid.UUID
    removed_seat_id: uuid.UUID | None = None


class StopSessionResponse(_Schema):
    """Остановка стола лидером: стол закрыт (идемпотентно stopped=True)."""

    stopped: bool = True


class ArchiveResponse(_Schema):
    """Безопасное удаление стола лидером: стол скрыт из списков, запись в БД остаётся."""

    archived: bool = True


class EventOut(_Schema):
    seq: int
    type: str
    visibility: str
    payload: dict[str, Any]


class JoinResponse(_Schema):
    session_id: uuid.UUID
    seat: SeatInfo


class SessionPreviewResponse(_Schema):
    """Публичное превью стола по invite-токену (задача #31): только карточка стола.

    Токен — capability, поэтому ответ не требует auth; dm-секреты (setup/state)
    сюда не попадают. ``seats_total`` — ёмкость стола (null = без ограничения).
    """

    id: uuid.UUID
    scenario: str
    scenario_title: str | None = None
    scenario_version_id: uuid.UUID | None = None
    sheet_template_id: uuid.UUID | None = None
    language: str
    dm_mode: str
    status: str
    seats_total: int | None = None
    seats_taken: int
    taken_character_ids: list[uuid.UUID] = Field(default_factory=list)
    host_name: str | None = None
    # Классы сценария для создания персонажа за столом; пусто — все классы.
    available_classes: list[str] = Field(default_factory=list)


class SessionResponse(_Schema):
    id: uuid.UUID
    scenario: str
    language: str
    dm_mode: str
    status: str
    last_seq: int
    state: dict[str, Any]
    seat: SeatInfo
    seats: list[SeatInfo]
    events: list[EventOut]
    # Счётчик наблюдателей (3.8): read-only места, не входят в ёмкость стола.
    spectators: int = 0
    # Лидерство определяется host_id сессии (не ролью места); host_id клиенту не отдаётся.
    is_host: bool
    # Тумблер предметной механики закреплённой версии сценария (подэтап 7):
    # False — фронтенд скрывает куклу и инвентарь.
    items_enabled: bool = True


class ItemCatalogResponse(_Schema):
    """Каталог предметов закреплённой версии сценария (подэтап 7, frontend-кукла).

    Полные определения Item (slot/icon/kind/consume_on/consumable_effect/attack/
    repair_hp), отфильтрованные по видимости места. ``items_enabled=False`` —
    механика выключена или версия не закреплена, список всегда пуст.
    """

    items_enabled: bool
    items: list[Item]


class EventsResponse(_Schema):
    events: list[EventOut]
    last_seq: int


class OpenTableItem(_Schema):
    """Стол с открытым набором в зале историй (задача #32, часть B).

    ``invite_token`` включён только для recruiting-столов: это capability для
    посадки из зала тем же механизмом, что и вход по приглашению (#31).
    """

    id: uuid.UUID
    scenario: str
    scenario_title: str | None = None
    language: str
    dm_mode: str
    seats_total: int | None = None
    seats_taken: int
    host_name: str | None = None
    invite_token: str


class RecruitingRequest(_Schema):
    """Лидер команды открывает/закрывает набор и задаёт ёмкость стола."""

    recruiting: bool
    seats_total: int | None = Field(default=None, ge=1, le=12)


class RecruitingResponse(_Schema):
    recruiting: bool
    seats_total: int | None = None


class ChatRequest(_Schema):
    """Сообщение чата вне игры: OOC-текст участника стола (не команда Мастеру)."""

    text: str = Field(min_length=1, max_length=500)


class ChatMessageOut(_Schema):
    id: uuid.UUID
    user_id: uuid.UUID
    author: str
    text: str
    at: datetime


class ChatHistoryResponse(_Schema):
    messages: list[ChatMessageOut]
