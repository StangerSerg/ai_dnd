"""Публикация сценария в базу и загрузка контента из базы (подэтап 2.1, архитектура 8.3/13).

Опубликованная версия неизменяема: сессия привязана к ``scenario_version_id``, поэтому
правка сценария (новая версия) не ломает идущую игру. Набор правил и шаблон карточки
тоже версионируются в базе и пиннятся ссылкой версии. Бестиарий и глоссарий — справочные
данные SRD, общие для правил; в прототипе они грузятся из ``content/`` (bounded decision).

Сущности хранятся по одной строке на сущность; при загрузке порядок детерминирован
(сортировка по ключу внутри типа). Движок ищет сущности по id, поэтому порядок не влияет
на игру, а детерминированность важна для стабильности кэша промпта (подэтап 2.3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_dnd.game.content import GameContent, load_game_content
from ai_dnd.game.skills import (
    CharacterClass,
    ClassPack,
    Skill,
    load_class_pack,
    load_classes_and_skills,
)
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rulesets.glossary import load_glossary
from ai_dnd.rulesets.models import RulesetRow
from ai_dnd.scenarios.models import ScenarioEntityRow, ScenarioRow, ScenarioVersionRow
from ai_dnd.scenarios.schemas import (
    Clue,
    Encounter,
    Handout,
    Item,
    Lore,
    Npc,
    Scenario,
    ScenarioInfo,
    ScenarioMap,
    Scene,
    Statblock,
)
from ai_dnd.scenarios.service import load_bestiary, read_yaml
from ai_dnd.sheets.compiled import compile_template
from ai_dnd.sheets.models import SheetTemplateRow
from ai_dnd.sheets.schemas import SheetTemplate

Sessions = async_sessionmaker[AsyncSession]


def _entities(scenario: Scenario) -> list[tuple[str, str, str | None, dict[str, Any], str]]:
    """Сценарий → строки сущностей: (тип, ключ, имя, данные, видимость)."""
    rows: list[tuple[str, str, str | None, dict[str, Any], str]] = []
    for scene in scenario.scenes:
        rows.append(("scene", scene.id, scene.title, scene.model_dump(mode="json"), "dm"))
    for npc in scenario.npcs:
        rows.append(("npc", npc.id, npc.name, npc.model_dump(mode="json"), "dm"))
    for item in scenario.items:
        rows.append(("item", item.id, item.name, item.model_dump(mode="json"), item.visibility))
    for clue in scenario.clues:
        rows.append(("clue", clue.id, None, clue.model_dump(mode="json"), clue.visibility))
    for handout in scenario.handouts:
        rows.append(
            (
                "handout",
                handout.id,
                handout.title,
                handout.model_dump(mode="json"),
                handout.visibility,
            )
        )
    for encounter in scenario.encounters:
        rows.append(
            ("encounter", encounter.id, encounter.title, encounter.model_dump(mode="json"), "dm")
        )
    for lore in scenario.lore:
        rows.append(("lore", lore.id, lore.title, lore.model_dump(mode="json"), lore.visibility))
    for key, statblock in scenario.statblocks.items():
        rows.append(("statblock", key, None, statblock.model_dump(mode="json"), "dm"))
    return rows


def _scenario_from_rows(
    info: dict[str, Any], map_data: dict[str, Any] | None, rows: list[ScenarioEntityRow]
) -> Scenario:
    by_type: dict[str, list[ScenarioEntityRow]] = {}
    for row in rows:
        by_type.setdefault(row.type, []).append(row)
    return Scenario(
        scenario=ScenarioInfo.model_validate(
            {k: v for k, v in info.items() if not k.startswith("_class_pack_")}
        ),
        scenes=tuple(Scene.model_validate(r.data) for r in by_type.get("scene", [])),
        npcs=tuple(Npc.model_validate(r.data) for r in by_type.get("npc", [])),
        items=tuple(Item.model_validate(r.data) for r in by_type.get("item", [])),
        clues=tuple(Clue.model_validate(r.data) for r in by_type.get("clue", [])),
        handouts=tuple(Handout.model_validate(r.data) for r in by_type.get("handout", [])),
        encounters=tuple(Encounter.model_validate(r.data) for r in by_type.get("encounter", [])),
        lore=tuple(Lore.model_validate(r.data) for r in by_type.get("lore", [])),
        statblocks={r.key: Statblock.model_validate(r.data) for r in by_type.get("statblock", [])},
        map=ScenarioMap.model_validate(map_data) if map_data is not None else None,
    )


def classes_for_version_info(info: dict[str, Any], content_dir: Path) -> dict[str, CharacterClass]:
    """Закреплённый снимок пакета; старые версии изолируются явным адаптером."""
    snapshot = info.get("_class_pack_snapshot")
    if snapshot is not None:
        pack = ClassPack.model_validate(snapshot)
        if pack.id != info.get("class_pack"):
            raise ValueError("Пакет классов версии не совпадает с её ссылкой")
        return pack.classes
    if info.get("class_pack") is not None:
        raise ValueError("Версия с class_pack не содержит закреплённого снимка пакета")
    # Версии, опубликованные до 2b, не хранили пакет. Соответствие ruleset→pack
    # перечислено явно; отсутствие inline-списка больше не означает «все».
    pack_id = _legacy_pack_id(info.get("ruleset"))
    classes = load_class_pack(content_dir, pack_id).classes
    inline = info.get("available_classes") or ()
    if inline:
        unknown = set(inline) - set(classes)
        if unknown:
            raise ValueError(f"У старой версии классы вне пакета: {sorted(unknown)}")
        return {key: value for key, value in classes.items() if key in inline}
    return classes


def _legacy_pack_id(ruleset_ref: object) -> str:
    pack_id = {"dnd5e-srd@1": "fantasy-core", "worms-space@1": "worms-ship"}.get(ruleset_ref)
    if pack_id is None:
        raise ValueError(f"Для старой версии неизвестен пакет классов: {ruleset_ref!r}")
    return pack_id


async def _upsert_ruleset(
    db: AsyncSession, ruleset: Ruleset, visibility: str, author_id: uuid.UUID | None
) -> RulesetRow:
    existing = await db.scalar(
        select(RulesetRow).where(
            RulesetRow.name == ruleset.id, RulesetRow.version == ruleset.version
        )
    )
    if existing is not None:
        return existing
    row = RulesetRow(
        author_id=author_id,
        name=ruleset.id,
        version=ruleset.version,
        data=ruleset.model_dump(mode="json"),
        visibility=visibility,
    )
    db.add(row)
    await db.flush()
    return row


async def _upsert_template(
    db: AsyncSession, template: SheetTemplate, visibility: str, author_id: uuid.UUID | None
) -> SheetTemplateRow:
    existing = await db.scalar(
        select(SheetTemplateRow).where(
            SheetTemplateRow.name == template.id, SheetTemplateRow.version == template.version
        )
    )
    if existing is not None:
        return existing
    row = SheetTemplateRow(
        author_id=author_id,
        name=template.id,
        version=template.version,
        data=template.model_dump(mode="json"),
        visibility=visibility,
    )
    db.add(row)
    await db.flush()
    return row


async def _insert_version(
    db: AsyncSession,
    scenario_row: ScenarioRow,
    scenario: Scenario,
    *,
    ruleset_id: uuid.UUID,
    sheet_template_id: uuid.UUID,
    info: dict[str, Any],
) -> ScenarioVersionRow:
    """Вставить новую неизменяемую версию со всеми сущностями и сделать её текущей."""
    last_number = await db.scalar(
        select(func.max(ScenarioVersionRow.number)).where(
            ScenarioVersionRow.scenario_id == scenario_row.id
        )
    )
    version = ScenarioVersionRow(
        scenario_id=scenario_row.id,
        number=(last_number or 0) + 1,
        ruleset_id=ruleset_id,
        sheet_template_id=sheet_template_id,
        info=info,
        map=scenario.map.model_dump(mode="json") if scenario.map is not None else None,
        published_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()
    for type_, key, name, data, entity_visibility in _entities(scenario):
        db.add(
            ScenarioEntityRow(
                version_id=version.id,
                type=type_,
                key=key,
                name=name,
                data=data,
                visibility=entity_visibility,
            )
        )
    scenario_row.current_version_id = version.id
    scenario_row.status = "published"
    return version


async def publish_scenario(
    sessions: Sessions,
    content_dir: Path,
    slug: str,
    language: str,
    *,
    visibility: str | None = None,
    new_visibility: str = "private",
    author_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Загрузить YAML, проверить и опубликовать новую неизменяемую версию. Вернуть её id."""
    content = load_game_content(content_dir, slug, language)
    scenario = content.scenario
    info = scenario.scenario
    # Ссылка шаблона в сценарии должна точно совпадать с загруженным шаблоном: файловый
    # загрузчик выбирает файл по части до «@», иначе версия закрепила бы чужой шаблон.
    if content.template.ref != info.sheet_template:
        raise ValueError(
            f"Ссылка шаблона {info.sheet_template!r} не совпадает с загруженной "
            f"{content.template.ref!r}; исправьте ссылку или файл шаблона"
        )
    async with sessions() as db:
        scenario_row = await db.scalar(
            select(ScenarioRow).where(ScenarioRow.slug == slug, ScenarioRow.language == language)
        )
        effective_visibility = (
            visibility
            if visibility is not None
            else scenario_row.visibility
            if scenario_row is not None
            else new_visibility
        )
        ruleset_row = await _upsert_ruleset(db, content.ruleset, effective_visibility, author_id)
        template_row = await _upsert_template(
            db, content.template.template, effective_visibility, author_id
        )
        if scenario_row is None:
            scenario_row = ScenarioRow(
                author_id=author_id,
                slug=slug,
                title=info.title,
                language=language,
                visibility=effective_visibility,
            )
            db.add(scenario_row)
            await db.flush()
        version = await _insert_version(
            db,
            scenario_row,
            scenario,
            ruleset_id=ruleset_row.id,
            sheet_template_id=template_row.id,
            info={
                **info.model_dump(mode="json"),
                "_class_pack_snapshot": load_class_pack(content_dir, info.class_pack).model_dump(
                    mode="json"
                )
                if info.class_pack is not None
                else None,
                "_class_pack_skills": {
                    key: value.model_dump(mode="json") for key, value in content.skills.items()
                },
            },
        )
        # Явно заданная видимость применяется и при повторной публикации.
        scenario_row.visibility = effective_visibility
        await db.commit()
        return version.id


async def publish_scenario_version(
    sessions: Sessions,
    scenario: Scenario,
    *,
    scenario_id: uuid.UUID,
    base_version_id: uuid.UUID,
) -> uuid.UUID:
    """Опубликовать отредактированный сценарий как новую неизменяемую версию (подэтап 7).

    Правила, шаблон карточки и снимок пакета классов наследуются от базовой версии:
    правки автора касаются только сущностей сценария. Старые сессии остаются привязаны
    к прежней версии и не видят правок.
    """
    async with sessions() as db:
        scenario_row = await db.get(ScenarioRow, scenario_id)
        if scenario_row is None:
            raise FileNotFoundError(f"Сценарий {scenario_id} не найден")
        base = await db.get(ScenarioVersionRow, base_version_id)
        if base is None or base.scenario_id != scenario_id:
            raise FileNotFoundError(
                f"Версия {base_version_id} не принадлежит сценарию {scenario_id}"
            )
        info = scenario.scenario
        version = await _insert_version(
            db,
            scenario_row,
            scenario,
            ruleset_id=base.ruleset_id,
            sheet_template_id=base.sheet_template_id,
            info={
                **info.model_dump(mode="json"),
                "_class_pack_snapshot": base.info.get("_class_pack_snapshot"),
                "_class_pack_skills": base.info.get("_class_pack_skills"),
            },
        )
        scenario_row.title = info.title
        await db.commit()
        return version.id


async def version_items_enabled(sessions: Sessions, scenario_version_id: uuid.UUID) -> bool:
    """Флаг предметной механики закреплённой версии сценария (по умолчанию включён)."""
    async with sessions() as db:
        info = await db.scalar(
            select(ScenarioVersionRow.info).where(ScenarioVersionRow.id == scenario_version_id)
        )
        if info is None:
            raise FileNotFoundError(f"Версия сценария {scenario_version_id} не найдена")
        return bool(info.get("items_enabled", True))


async def scenario_visibility(sessions: Sessions, scenario_version_id: uuid.UUID) -> str:
    """Текущая видимость сценария, которому принадлежит версия."""
    async with sessions() as db:
        visibility = await db.scalar(
            select(ScenarioRow.visibility)
            .join(ScenarioVersionRow, ScenarioVersionRow.scenario_id == ScenarioRow.id)
            .where(ScenarioVersionRow.id == scenario_version_id)
        )
        if visibility is None:
            raise FileNotFoundError(f"Версия сценария {scenario_version_id} не найдена")
        return visibility


async def resolve_current_version(sessions: Sessions, slug: str, language: str) -> uuid.UUID:
    """Id текущей опубликованной версии сценария по slug и языку."""
    async with sessions() as db:
        scenario_row = await db.scalar(
            select(ScenarioRow).where(ScenarioRow.slug == slug, ScenarioRow.language == language)
        )
        if scenario_row is None or scenario_row.current_version_id is None:
            raise FileNotFoundError(f"Сценарий {slug}.{language} не опубликован в базе")
        return scenario_row.current_version_id


async def version_refs(
    sessions: Sessions, scenario_version_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """(ruleset_id, sheet_template_id) версии — для привязки игровой сессии."""
    async with sessions() as db:
        version = await db.get(ScenarioVersionRow, scenario_version_id)
        if version is None:
            raise FileNotFoundError(f"Версия сценария {scenario_version_id} не найдена")
        return version.ruleset_id, version.sheet_template_id


async def version_language(sessions: Sessions, scenario_version_id: uuid.UUID) -> str:
    """Язык сценария, которому принадлежит версия."""
    async with sessions() as db:
        language = await db.scalar(
            select(ScenarioRow.language)
            .join(ScenarioVersionRow, ScenarioVersionRow.scenario_id == ScenarioRow.id)
            .where(ScenarioVersionRow.id == scenario_version_id)
        )
        if language is None:
            raise FileNotFoundError(f"Версия сценария {scenario_version_id} не найдена")
        return language


async def load_game_content_from_db(
    sessions: Sessions, scenario_version_id: uuid.UUID, content_dir: Path
) -> GameContent:
    """Собрать GameContent из неизменяемой версии в базе (правила и шаблон пиннятся)."""
    async with sessions() as db:
        version = await db.get(ScenarioVersionRow, scenario_version_id)
        if version is None:
            raise FileNotFoundError(f"Версия сценария {scenario_version_id} не найдена")
        ruleset_row = await db.get(RulesetRow, version.ruleset_id)
        template_row = await db.get(SheetTemplateRow, version.sheet_template_id)
        rows = list(
            (
                await db.scalars(
                    select(ScenarioEntityRow)
                    .where(ScenarioEntityRow.version_id == scenario_version_id)
                    .order_by(ScenarioEntityRow.type, ScenarioEntityRow.key)
                )
            ).all()
        )
    if ruleset_row is None or template_row is None:
        raise FileNotFoundError(f"Правила/шаблон версии {scenario_version_id} не найдены")
    ruleset = Ruleset.model_validate(ruleset_row.data)
    template = compile_template(SheetTemplate.model_validate(template_row.data))
    scenario = _scenario_from_rows(version.info, version.map, rows)
    bestiaries = tuple(
        load_bestiary(path, ruleset)
        for path in sorted((content_dir / "bestiary").glob("*.yaml"))
        if read_yaml(path).get("ruleset") == ruleset.ref
    )
    classes = classes_for_version_info(version.info, content_dir)
    skill_snapshot = version.info.get("_class_pack_skills")
    if skill_snapshot is not None:
        skills = TypeAdapter(dict[str, Skill]).validate_python(skill_snapshot)
    else:
        _, skills = load_classes_and_skills(
            content_dir, scenario.scenario.class_pack or _legacy_pack_id(scenario.scenario.ruleset)
        )
    return GameContent(
        ruleset=ruleset,
        template=template,
        bestiaries=bestiaries,
        scenario=scenario,
        glossary=load_glossary(content_dir / "glossary.yaml"),
        classes=classes,
        skills=skills,
    )
