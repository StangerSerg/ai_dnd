"""REST автора сценария (подэтап 7): просмотр и правка предметов опубликованного сценария.

Доступ — только автор строки сценария (``scenarios.author_id``); сценарии без автора
(импорт из контента) через этот API не редактируются. Правка предмета публикует НОВУЮ
неизменяемую версию (``publish_scenario_version``): правила, шаблон и пакет классов
наследуются от базовой версии, идущие сессии остаются привязаны к прежней версии.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_dnd.config import get_settings
from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.resources import Resources, get_resources
from ai_dnd.rules.ruleset import Effect
from ai_dnd.scenarios.models import ScenarioRow
from ai_dnd.scenarios.schemas import (
    ContentVisibility,
    EncounterGroup,
    EntityId,
    Item,
    ItemAttack,
    ItemConsumeTrigger,
    ItemKind,
    ItemSlot,
    ItemUseEffect,
    LootEntry,
    _Line,
    _Text,
)
from ai_dnd.scenarios.service import find_scenario_issues
from ai_dnd.scenarios.store import load_game_content_from_db, publish_scenario_version


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorScenario(_Schema):
    id: uuid.UUID
    slug: str
    language: str
    title: str
    visibility: str
    current_version_id: uuid.UUID | None


class IssueInfo(_Schema):
    code: str
    path: str
    params: dict[str, Any] = Field(default_factory=dict)


class EncounterInfo(_Schema):
    id: str
    title: str
    creatures: list[EncounterGroup]
    loot: list[LootEntry]
    victory_facts: list[str] = Field(default_factory=list)


class AuthorItemsResponse(_Schema):
    version_id: uuid.UUID
    items: list[Item]
    encounters: list[EncounterInfo]
    issues: list[IssueInfo]


class ItemUpdateRequest(_Schema):
    """Правка предмета: пропущенные поля сохраняют значение, явный null сбрасывает."""

    name: _Line | None = None
    description: _Text | None = None
    visibility: ContentVisibility | None = None
    kind: ItemKind | None = None
    slot: ItemSlot | None = None
    quantity: Annotated[int, Field(ge=1)] | None = None
    modifiers: tuple[Effect, ...] | None = None
    ac_bonus: int | None = None
    damage_bonus: int | None = None
    damage_type: EntityId | None = None
    enhancement: Annotated[int, Field(ge=0)] | None = None
    consumable_effect: ItemUseEffect | None = None
    consume_on: ItemConsumeTrigger | None = None
    ammo: EntityId | None = None
    attack: ItemAttack | None = None
    repair_hp: Annotated[int, Field(ge=1)] | None = None
    icon: EntityId | None = None


class ItemUpdateResponse(_Schema):
    version_id: uuid.UUID
    item: Item
    issues: list[IssueInfo]


def _issue_info(code: str, path: str, params: dict[str, Any]) -> IssueInfo:
    return IssueInfo(code=code, path=path, params=params)


async def _own_scenario(db: AsyncSession, user: CurrentUser, scenario_id: uuid.UUID) -> ScenarioRow:
    row = await db.get(ScenarioRow, scenario_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scenario_not_found")
    if row.author_id is None or row.author_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_scenario_author")
    return row


def build_authoring_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/author", tags=["author"])

    @router.get("/scenarios", response_model=list[AuthorScenario])
    async def list_author_scenarios(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> list[AuthorScenario]:
        async with resources.db_sessions() as db:
            rows = (
                await db.scalars(
                    select(ScenarioRow)
                    .where(ScenarioRow.author_id == user.user_id)
                    .order_by(ScenarioRow.slug, ScenarioRow.language)
                )
            ).all()
            return [
                AuthorScenario(
                    id=row.id,
                    slug=row.slug,
                    language=row.language,
                    title=row.title,
                    visibility=row.visibility,
                    current_version_id=row.current_version_id,
                )
                for row in rows
            ]

    @router.get("/scenarios/{scenario_id}/items", response_model=AuthorItemsResponse)
    async def get_author_items(
        scenario_id: uuid.UUID,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> AuthorItemsResponse:
        async with resources.db_sessions() as db:
            row = await _own_scenario(db, user, scenario_id)
            version_id = row.current_version_id
        if version_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="scenario_not_published"
            )
        settings = get_settings()
        content = await load_game_content_from_db(
            resources.db_sessions, version_id, settings.content_dir
        )
        issues = find_scenario_issues(
            content.scenario,
            content.bestiaries,
            content.ruleset,
            classes=content.classes,
            content_dir=settings.content_dir,
        )
        return AuthorItemsResponse(
            version_id=version_id,
            items=list(content.scenario.items),
            encounters=[
                EncounterInfo(
                    id=encounter.id,
                    title=encounter.title,
                    creatures=list(encounter.creatures),
                    loot=list(encounter.loot),
                    victory_facts=list(encounter.victory_facts),
                )
                for encounter in content.scenario.encounters
            ],
            issues=[_issue_info(issue.code, issue.path, dict(issue.params)) for issue in issues],
        )

    @router.put("/scenarios/{scenario_id}/items/{item_id}", response_model=ItemUpdateResponse)
    async def update_author_item(
        scenario_id: uuid.UUID,
        item_id: str,
        body: ItemUpdateRequest,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> ItemUpdateResponse:
        async with resources.db_sessions() as db:
            row = await _own_scenario(db, user, scenario_id)
            version_id = row.current_version_id
        if version_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="scenario_not_published"
            )
        settings = get_settings()
        content = await load_game_content_from_db(
            resources.db_sessions, version_id, settings.content_dir
        )
        old_item = next((item for item in content.scenario.items if item.id == item_id), None)
        if old_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item_not_found")
        merged = {
            **old_item.model_dump(mode="json"),
            **body.model_dump(mode="json", exclude_unset=True),
        }
        try:
            new_item = Item.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_item",
                    "issues": [
                        {
                            "code": "schema_error",
                            "path": ".".join(str(part) for part in error["loc"]),
                            "params": {"message": error["msg"]},
                        }
                        for error in exc.errors()
                    ],
                },
            ) from exc
        new_scenario = content.scenario.model_copy(
            update={
                "items": tuple(
                    new_item if item.id == item_id else item for item in content.scenario.items
                )
            }
        )
        issues = find_scenario_issues(
            new_scenario,
            content.bestiaries,
            content.ruleset,
            classes=content.classes,
            content_dir=settings.content_dir,
        )
        if issues:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_scenario",
                    "issues": [
                        {
                            "code": issue.code,
                            "path": issue.path,
                            "params": dict(issue.params),
                        }
                        for issue in issues
                    ],
                },
            )
        new_version_id = await publish_scenario_version(
            resources.db_sessions,
            new_scenario,
            scenario_id=scenario_id,
            base_version_id=version_id,
        )
        return ItemUpdateResponse(version_id=new_version_id, item=new_item, issues=[])

    return router
