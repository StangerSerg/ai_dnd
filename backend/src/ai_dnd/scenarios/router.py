"""Read-API каталога сценариев (3.4): опубликованные public-версии для выбора игры.

Роутер собирается фабрикой: аутентификация — зависимость-seam ``GetCurrentUser``
(см. ``realtime.auth``), как у realtime.router. Отдаётся текущая опубликованная
версия каждого public-сценария (одна на slug+язык); title — из контента версии
(``scenario_versions.info``), не из редактируемой строки сценария.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ai_dnd.realtime.auth import CurrentUser, GetCurrentUser
from ai_dnd.resources import Resources, get_resources
from ai_dnd.scenarios.models import ScenarioRow, ScenarioVersionRow


class ScenarioListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID
    slug: str
    language: str
    title: str


def build_scenarios_router(get_current_user: GetCurrentUser) -> APIRouter:
    router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

    @router.get("", response_model=list[ScenarioListItem])
    async def list_scenarios(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> list[ScenarioListItem]:
        async with resources.db_sessions() as db:
            rows = (
                await db.execute(
                    select(
                        ScenarioVersionRow.id,
                        ScenarioRow.slug,
                        ScenarioRow.language,
                        ScenarioVersionRow.info,
                    )
                    .join(ScenarioRow, ScenarioRow.current_version_id == ScenarioVersionRow.id)
                    .where(ScenarioRow.visibility == "public", ScenarioRow.status == "published")
                    .order_by(ScenarioRow.slug, ScenarioRow.language)
                )
            ).all()
        return [
            ScenarioListItem(
                version_id=version_id,
                slug=slug,
                language=language,
                title=str(info.get("title") or slug),
            )
            for version_id, slug, language, info in rows
        ]

    return router
