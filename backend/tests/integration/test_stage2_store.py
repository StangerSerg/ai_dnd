"""Подэтап 2.1: публикация неизменяемых версий, round-trip, pinning сессий, видимость.

Запуск: pytest tests/integration -m integration (нужны PostgreSQL+pgvector из infra/).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.game.content import load_game_content
from ai_dnd.game.models import GameSessionRow
from ai_dnd.scenarios.models import ScenarioEntityRow, ScenarioVersionRow
from ai_dnd.scenarios.store import (
    load_game_content_from_db,
    publish_scenario,
    resolve_current_version,
    scenario_visibility,
    version_refs,
)

from .stage2_helpers import canon_content

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]


@pytest.mark.parametrize("language", ["ru", "en"])
async def test_roundtrip_matches_yaml(
    sessions: Sessions, scenario_dir: tuple[Path, str], language: str
) -> None:
    content_path, slug = scenario_dir
    version = await publish_scenario(sessions, content_path, slug, language, visibility="public")

    from_file = load_game_content(content_path, slug, language)
    from_db = await load_game_content_from_db(sessions, version, content_path)

    assert canon_content(from_db) == canon_content(from_file)
    assert from_db.ruleset.ref == from_file.ruleset.ref
    assert from_db.template.ref == from_file.template.ref


async def test_republish_creates_new_version_old_immutable(
    sessions: Sessions, scenario_dir: tuple[Path, str]
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    v2 = await publish_scenario(sessions, content_path, slug, "ru")
    assert v2 != v1

    async with sessions() as db:
        numbers = dict(
            (
                await db.execute(
                    select(ScenarioVersionRow.id, ScenarioVersionRow.number).where(
                        ScenarioVersionRow.id.in_([v1, v2])
                    )
                )
            ).all()
        )
    assert numbers == {v1: 1, v2: 2}
    assert await resolve_current_version(sessions, slug, "ru") == v2

    from_file = load_game_content(content_path, slug, "ru")
    # Старая версия не изменилась после публикации новой и всё ещё загружается.
    assert canon_content(await load_game_content_from_db(sessions, v1, content_path)) == (
        canon_content(from_file)
    )
    assert canon_content(await load_game_content_from_db(sessions, v2, content_path)) == (
        canon_content(from_file)
    )
    # Правила и шаблон переиспользуются, а не плодятся при каждой публикации.
    assert await version_refs(sessions, v1) == await version_refs(sessions, v2)


async def test_session_stays_pinned_to_its_version(
    sessions: Sessions, scenario_dir: tuple[Path, str]
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    ruleset_id, template_id = await version_refs(sessions, v1)
    session_id = uuid.uuid4()
    async with sessions() as db:
        db.add(
            GameSessionRow(
                id=session_id,
                scenario=slug,
                language="ru",
                setup={},
                state={},
                scenario_version_id=v1,
                ruleset_id=ruleset_id,
                sheet_template_id=template_id,
            )
        )
        await db.commit()

    v2 = await publish_scenario(sessions, content_path, slug, "ru")
    assert v2 != v1
    assert await resolve_current_version(sessions, slug, "ru") == v2

    async with sessions() as db:
        row = await db.scalar(select(GameSessionRow).where(GameSessionRow.id == session_id))
    assert row is not None
    assert row.scenario_version_id == v1
    assert row.ruleset_id == ruleset_id
    assert row.sheet_template_id == template_id
    # Контент запинненной версии по-прежнему собирается из базы.
    pinned = await load_game_content_from_db(sessions, v1, content_path)
    assert canon_content(pinned) == canon_content(load_game_content(content_path, slug, "ru"))


async def test_visibility_scenario_and_entities(
    sessions: Sessions, scenario_dir: tuple[Path, str]
) -> None:
    content_path, slug = scenario_dir
    v1 = await publish_scenario(sessions, content_path, slug, "ru")
    assert await scenario_visibility(sessions, v1) == "private"

    v2 = await publish_scenario(sessions, content_path, slug, "ru", visibility="public")
    # Видимость — на уровне сценария: применяется и к уже опубликованным версиям.
    assert await scenario_visibility(sessions, v1) == "public"
    assert await scenario_visibility(sessions, v2) == "public"

    async with sessions() as db:
        rows = list(
            await db.scalars(select(ScenarioEntityRow).where(ScenarioEntityRow.version_id == v1))
        )
    visibility = {(row.type, row.key): row.visibility for row in rows}
    assert visibility[("lore", "village")] == "public"
    assert visibility[("lore", "serpent_cult")] == "dm"
    assert visibility[("item", "ritual_bowl")] == "public"
    assert visibility[("item", "cellar_key")] == "dm"
    assert visibility[("handout", "reward_notice")] == "public"
    assert visibility[("handout", "cult_letter")] == "dm"
    for (type_, _key), value in visibility.items():
        if type_ in ("scene", "npc", "encounter", "statblock", "clue"):
            assert value == "dm", (type_, _key)


async def test_unknown_slug_and_version_raise(
    sessions: Sessions, scenario_dir: tuple[Path, str]
) -> None:
    content_path, slug = scenario_dir
    missing = uuid.uuid4()
    with pytest.raises(FileNotFoundError):
        await resolve_current_version(sessions, f"{slug}-missing", "ru")
    with pytest.raises(FileNotFoundError):
        await scenario_visibility(sessions, missing)
    with pytest.raises(FileNotFoundError):
        await version_refs(sessions, missing)
    with pytest.raises(FileNotFoundError):
        await load_game_content_from_db(sessions, missing, content_path)


async def test_template_ref_mismatch_rejected(
    sessions: Sessions, scenario_dir: tuple[Path, str]
) -> None:
    content_path, slug = scenario_dir
    yaml_path = content_path / "scenarios" / f"{slug}.ru.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "sheet_template: srd5-basic@1", "sheet_template: srd5-basic@2"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Ссылка шаблона"):
        await publish_scenario(sessions, content_path, slug, "ru")
