"""API персонажей (3.4): список моих, создание по версии сценария, лист owner-а.

Роутер собирается фабрикой: аутентификация — зависимость-seam ``GetCurrentUser``
(см. ``realtime.auth``), как у realtime.router. Лист отдаёт JSON шаблона карточки
в том виде, в котором его хранит ``sheet_templates.data`` (sheets.schemas.SheetTemplate):
фронтенд рисует поля по ``fields``/``layout``, значения берутся по id поля.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ai_dnd.characters.avatars import validate_portrait
from ai_dnd.characters.models import CharacterRow
from ai_dnd.characters.schemas import Character
from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.config import get_settings
from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.resources import Resources, get_resources
from ai_dnd.scenarios.store import version_language, version_refs
from ai_dnd.sheets.compiled import compile_template
from ai_dnd.sheets.issues import SheetValuesError
from ai_dnd.sheets.models import SheetTemplateRow
from ai_dnd.sheets.schemas import SheetTemplate


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterListItem(_Schema):
    id: uuid.UUID
    name: str
    template_id: uuid.UUID
    portrait: str | None
    updated_at: datetime


# Бета-классы: готовые архетипы 3-го уровня из content/characters/<lang>/<class>.json.
CharacterClass = Literal["fighter", "rogue", "cleric", "ranger"]


class CreateCharacterRequest(_Schema):
    name: str = Field(min_length=1, max_length=120)
    scenario_version_id: uuid.UUID
    char_class: CharacterClass
    # Раса — косметический текст; None/пусто — остаётся раса архетипа.
    race: str | None = Field(default=None, max_length=60)
    # Путь пресета из characters.avatars.AVATAR_PRESETS; None — без аватара.
    portrait: str | None = None


class UpdateCharacterRequest(_Schema):
    # None очищает аватар; иначе путь обязан быть пресетом (422 invalid_portrait).
    portrait: str | None


class CharacterSheetResponse(_Schema):
    id: uuid.UUID
    name: str
    portrait: str | None
    # JSON шаблона листа (SheetTemplate): fields, layout, bindings — фронтенд рисует по нему.
    template: dict[str, Any]
    # Значения полей персонажа по id (хранимая часть data).
    values: dict[str, Any]


def _item(row: CharacterRow) -> CharacterListItem:
    return CharacterListItem(
        id=row.id,
        name=row.name,
        template_id=row.template_id,
        portrait=row.portrait,
        updated_at=row.updated_at,
    )


def _archetype_values(char_class: CharacterClass, language: str) -> dict[str, Any]:
    """Базовые значения готового архетипа класса (оружие, характеристики, доспех, заклинания)."""
    characters_dir = get_settings().content_dir / "characters"
    path = characters_dir / language / f"{char_class}.json"
    if not path.is_file():
        # Фолбэк на ru: en/ranger.json пока нет, а ru-архетипы есть для всех классов;
        # id полей шаблона совпадают между языками, так что значения валидны.
        path = characters_dir / "ru" / f"{char_class}.json"
    return dict(load_character(path).values)


def build_characters_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/characters", tags=["characters"])

    @router.get("", response_model=list[CharacterListItem])
    async def list_characters(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> list[CharacterListItem]:
        async with resources.db_sessions() as db:
            rows = (
                await db.scalars(
                    select(CharacterRow)
                    .where(CharacterRow.owner_id == user.user_id)
                    .order_by(CharacterRow.updated_at.desc())
                )
            ).all()
        return [_item(row) for row in rows]

    @router.post("", response_model=CharacterListItem, status_code=201)
    async def create_character(
        body: CreateCharacterRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CharacterListItem:
        """Новый персонаж: шаблон пиннится версией сценария, значения — архетип класса."""
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "empty_name")
        race = body.race.strip() if body.race else ""
        portrait = validate_portrait(body.portrait)
        sessions = resources.db_sessions
        try:
            _, template_id = await version_refs(sessions, body.scenario_version_id)
            language = await version_language(sessions, body.scenario_version_id)
        except FileNotFoundError as error:
            raise HTTPException(404, "scenario_version_not_found") from error
        async with sessions() as db:
            template_row = await db.get(SheetTemplateRow, template_id)
        if template_row is None:
            raise HTTPException(404, "sheet_template_not_found")
        template = compile_template(SheetTemplate.model_validate(template_row.data))
        values = _archetype_values(body.char_class, language)
        values["name"] = name
        if race:
            values["race"] = race
        try:
            # Как realtime service.join: значения проверяются и дополняются шаблоном.
            sheet = build_sheet(Character(template=template.ref, values=values), template)
        except SheetValuesError as error:
            raise HTTPException(422, "character_invalid") from error
        data = Character(
            template=template.ref, values=sheet.values, class_id=body.char_class
        ).model_dump(mode="json")
        async with sessions() as db, db.begin():
            row = CharacterRow(
                owner_id=user.user_id,
                template_id=template_id,
                name=name,
                data=data,
                portrait=portrait,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
        return _item(row)

    @router.get("/{character_id}", response_model=CharacterSheetResponse)
    async def get_character_sheet(
        character_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CharacterSheetResponse:
        sessions = resources.db_sessions
        async with sessions() as db:
            row = await db.get(CharacterRow, character_id)
        if row is None:
            raise HTTPException(404, "character_not_found")
        if row.owner_id != user.user_id:
            raise HTTPException(403, "not_owner")
        async with sessions() as db:
            template_row = await db.get(SheetTemplateRow, row.template_id)
        if template_row is None:
            raise HTTPException(404, "sheet_template_not_found")
        character = Character.model_validate(row.data)
        return CharacterSheetResponse(
            id=row.id,
            name=row.name,
            portrait=row.portrait,
            template=template_row.data,
            values=character.values,
        )

    @router.patch("/{character_id}", response_model=CharacterListItem)
    async def update_character(
        character_id: uuid.UUID,
        body: UpdateCharacterRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> CharacterListItem:
        """Смена аватара (owner-only): пресет из allowlist либо None — очистить."""
        async with resources.db_sessions() as db, db.begin():
            row = await db.get(CharacterRow, character_id)
            if row is None:
                raise HTTPException(404, "character_not_found")
            if row.owner_id != user.user_id:
                raise HTTPException(403, "not_owner")
            row.portrait = validate_portrait(body.portrait)
            row.updated_at = datetime.now(UTC)
            await db.flush()
            await db.refresh(row)
        return _item(row)

    return router
