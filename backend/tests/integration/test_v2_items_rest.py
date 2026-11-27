"""Подэтап 9: предметный REST v0.2 на сервисном уровне с реальной одноразовой БД.

Авторский API (scenarios/authoring.py), каталог предметов (realtime/router.py) и
предметные действия игрока (realtime/turns.py) вызываются напрямую как функции
эндпоинтов — HTTP-сервер не поднимается. Замок комнаты и pub/sub подменены
стаб-Redis по образцу tests/game/test_combat_autostart.py; реальная БД — только
ai_dnd_test из фикстур tests/integration/conftest.py. Сценарий five-guardians
публикуется под уникальным slug во временной копии content/, строки удаляются
после теста. Биллинг не затрагивается: LLM-вызовов нет.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from ai_dnd.config import get_settings
from ai_dnd.game.content import GameContent, load_party, new_session_state
from ai_dnd.game.events import JOURNAL_EVENT, ItemGranted, Revealed
from ai_dnd.game.journal import DbJournal
from ai_dnd.game.models import GameEventRow, GameSessionRow
from ai_dnd.game.session import SessionSetup, SessionState, apply_events, item_stock
from ai_dnd.identity.models import UserRow
from ai_dnd.realtime import router as realtime_router
from ai_dnd.realtime import turns
from ai_dnd.realtime.auth import CurrentUser
from ai_dnd.realtime.broadcast import BroadcastJournal
from ai_dnd.realtime.models import SeatRow
from ai_dnd.realtime.turns import ItemActionRequest, lock_key
from ai_dnd.resources import Resources
from ai_dnd.rules.events import CombatStarted
from ai_dnd.rules.rng import SeededRng
from ai_dnd.scenarios import authoring
from ai_dnd.scenarios.models import ScenarioRow
from ai_dnd.scenarios.schemas import (
    CheckResult,
    Item,
    ItemAttack,
    ItemHeal,
    LootEntry,
    Scenario,
    SceneCheck,
)
from ai_dnd.scenarios.store import (
    load_game_content_from_db,
    publish_scenario,
    publish_scenario_version,
    version_refs,
)

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]


class _FakeRedis:
    """Замок комнаты и публикация событий: set/get/exists/delete/eval/publish в памяти."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self._data:
            return None
        self._data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def exists(self, key: str) -> int:
        return int(key in self._data)

    async def delete(self, *keys: str) -> int:
        removed = sum(key in self._data for key in keys)
        for key in keys:
            self._data.pop(key, None)
        return removed

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        # _RELEASE_LUA снимает замок только владельцем токена.
        key, token = str(args[0]), str(args[1])
        if self._data.get(key) == token:
            del self._data[key]
            return 1
        return 0

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0


async def _unused_auth(connection: HTTPConnection) -> CurrentUser:
    """Заглушка seam'а аутентификации: эндпоинты вызываются с явным user."""
    raise AssertionError("auth seam не используется при прямом вызове эндпоинта")


def _endpoint(router: APIRouter, path: str, method: str) -> Callable[..., Awaitable[Any]]:
    """Функция эндпоинта роутера — вызывается напрямую, без HTTP."""
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"Роутер не содержит {method} {path}")


@dataclass
class _Env:
    sessions: Sessions
    content_path: Path
    slug: str
    scenario_id: uuid.UUID
    noauthor_scenario_id: uuid.UUID
    author: CurrentUser
    other: CurrentUser
    watcher: CurrentUser
    redis: _FakeRedis
    resources: Resources
    v1: uuid.UUID
    v2: uuid.UUID
    content: GameContent
    ruleset_id: uuid.UUID
    template_id: uuid.UUID
    author_router: APIRouter
    catalog_router: APIRouter
    turns_router: APIRouter


@dataclass
class _Room:
    session_id: uuid.UUID
    initial: SessionState
    journal: BroadcastJournal


def _mechanical_fixture(base: GameContent) -> Scenario:
    """Сценарий v2: механические предметы и чековая находка в стартовой сцене (как смоук 7)."""
    scenario = base.scenario
    start = next(s for s in scenario.scenes if s.id == scenario.scenario.start_scene)
    new_items = (
        Item(
            id="steel_sword",
            name="Стальной меч",
            kind="weapon",
            slot="weapon",
            icon="sword",
            attack=ItemAttack(name="Удар мечом", bonus=4, damage="1d8+2"),
        ),
        Item(
            id="healing_potion",
            name="Зелье лечения",
            kind="consumable",
            quantity=2,
            icon="potion",
            consumable_effect=ItemHeal(type="heal", amount="2d4+2"),
            consume_on="use",
        ),
        Item(
            id="repair_parts",
            name="Запчасти",
            kind="other",
            quantity=3,
            icon="ammo",
            consume_on="spend",
            repair_hp=5,
        ),
        # dm-only: не выдан и не раскрыт — игрок не видит, ведущий видит.
        Item(id="dm_orders", name="Тайные приказы", visibility="dm", icon="scroll"),
        # dm-only, но выдан игроку: владение раскрывает определение владельцу.
        Item(id="dm_signet", name="Перстень тени", visibility="dm", icon="ring"),
    )
    return scenario.model_copy(
        update={
            "items": (*scenario.items, *new_items),
            "scenes": tuple(
                s.model_copy(
                    update={
                        "items": (*s.items, "steel_sword", "healing_potion", "repair_parts"),
                        "checks": (
                            *s.checks,
                            SceneCheck(
                                id="search_cart",
                                skill="perception",
                                dc=1,
                                on_success=CheckResult(reveal=("steel_sword",)),
                            ),
                        ),
                    }
                )
                if s.id == start.id
                else s
                for s in scenario.scenes
            ),
        }
    )


@pytest.fixture
async def items_env(
    tmp_path: Path,
    content_dir: Path,
    db_engine: AsyncEngine,
    sessions: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_Env]:
    """Временная копия content/ с five-guardians под уникальным slug + очистка строк."""
    slug = f"it7-{uuid.uuid4().hex[:12]}"
    noauthor_slug = f"{slug}-na"
    target = tmp_path / "content"
    for sub in ("rulesets", "sheets", "bestiary", "classes", "characters"):
        shutil.copytree(content_dir / sub, target / sub)
    for name in ("glossary.yaml", "skills.yaml"):
        shutil.copy2(content_dir / name, target / name)
    scenarios = target / "scenarios"
    scenarios.mkdir()
    shutil.copy2(
        content_dir / "scenarios" / "five-guardians.ru.yaml", scenarios / f"{slug}.ru.yaml"
    )
    shutil.copy2(
        content_dir / "scenarios" / "shadow-over-millford.ru.yaml",
        scenarios / f"{noauthor_slug}.ru.yaml",
    )
    # Эндпоинты читают content_dir из кэшированных настроек — подменяем на копию.
    monkeypatch.setattr(get_settings(), "content_dir", target)
    # Предметные действия считают лечение движком: детерминированный генератор.
    monkeypatch.setattr(turns, "SystemRng", lambda: SeededRng(7))

    author_id, other_id, watcher_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    redis = _FakeRedis()
    resources = Resources(db_engine=db_engine, db_sessions=sessions, redis=cast(Redis, redis))
    try:
        async with sessions() as db:
            db.add(UserRow(id=author_id, display_name="Author"))
            db.add(UserRow(id=other_id, display_name="Other"))
            db.add(UserRow(id=watcher_id, display_name="Watcher"))
            await db.commit()
        v1 = await publish_scenario(
            sessions, target, slug, "ru", visibility="private", author_id=author_id
        )
        async with sessions() as db:
            scenario_id = await db.scalar(
                select(ScenarioRow.id).where(ScenarioRow.slug == slug, ScenarioRow.language == "ru")
            )
        assert scenario_id is not None
        base = await load_game_content_from_db(sessions, v1, target)
        v2 = await publish_scenario_version(
            sessions, _mechanical_fixture(base), scenario_id=scenario_id, base_version_id=v1
        )
        content = await load_game_content_from_db(sessions, v2, target)
        ruleset_id, template_id = await version_refs(sessions, v2)
        # Сценарий без автора (импорт из контента): правка через API запрещена.
        await publish_scenario(sessions, target, noauthor_slug, "ru", visibility="private")
        async with sessions() as db:
            noauthor_id = await db.scalar(
                select(ScenarioRow.id).where(
                    ScenarioRow.slug == noauthor_slug, ScenarioRow.language == "ru"
                )
            )
        assert noauthor_id is not None
        yield _Env(
            sessions=sessions,
            content_path=target,
            slug=slug,
            scenario_id=scenario_id,
            noauthor_scenario_id=noauthor_id,
            author=CurrentUser(user_id=author_id, display_name="Author"),
            other=CurrentUser(user_id=other_id, display_name="Other"),
            watcher=CurrentUser(user_id=watcher_id, display_name="Watcher"),
            redis=redis,
            resources=resources,
            v1=v1,
            v2=v2,
            content=content,
            ruleset_id=ruleset_id,
            template_id=template_id,
            author_router=authoring.build_authoring_router(_unused_auth),
            catalog_router=realtime_router.build_router(_unused_auth),
            turns_router=turns.build_turns_router(_unused_auth),
        )
    finally:
        async with sessions() as db:
            # Сессии ссылаются на версии без CASCADE — удаляем первыми; места и
            # журнал каскадом от сессий, версии/сущности — каскадом от сценариев.
            await db.execute(
                delete(GameSessionRow).where(GameSessionRow.scenario.in_([slug, noauthor_slug]))
            )
            await db.execute(delete(ScenarioRow).where(ScenarioRow.slug.in_([slug, noauthor_slug])))
            await db.execute(
                delete(UserRow).where(UserRow.id.in_([author_id, other_id, watcher_id]))
            )
            await db.commit()


async def _make_room(
    env: _Env,
    *,
    version_id: uuid.UUID | None = None,
    content: GameContent | None = None,
    grants: bool = True,
) -> _Room:
    """Сессия в БД: player-место воина у автора, dm-место у другого, spectator у третьего."""
    content = content if content is not None else env.content
    seats, actors = load_party(env.content_path, content, ["fighter", "rogue"])
    setup = SessionSetup(scenario=env.slug, language="ru", improvisation="free", seats=tuple(seats))
    initial = new_session_state(content, actors)
    session_id = uuid.uuid4()
    async with env.sessions() as db:
        db.add(
            GameSessionRow(
                id=session_id,
                scenario=env.slug,
                language="ru",
                dm_mode="ai",
                status="active",
                setup=setup.model_dump(mode="json"),
                state=initial.model_dump(mode="json"),
                scenario_version_id=env.v2 if version_id is None else version_id,
                ruleset_id=env.ruleset_id,
                sheet_template_id=env.template_id,
                host_id=None,
                last_seq=0,
            )
        )
        db.add(
            SeatRow(
                session_id=session_id,
                role="player",
                controller="human",
                user_id=env.author.user_id,
                actor_id="fighter",
            )
        )
        db.add(
            SeatRow(
                session_id=session_id,
                role="dm",
                controller="human",
                user_id=env.other.user_id,
                actor_id="dm",
            )
        )
        db.add(
            SeatRow(
                session_id=session_id,
                role="spectator",
                controller="human",
                user_id=env.watcher.user_id,
                actor_id="spectator",
            )
        )
        await db.commit()
    journal = BroadcastJournal(
        DbJournal(env.sessions, session_id), cast(Redis, env.redis), env.sessions
    )
    if grants:
        events = [
            ItemGranted(actor_id="fighter", item_id="steel_sword", name="Стальной меч"),
            ItemGranted(
                actor_id="fighter", item_id="healing_potion", name="Зелье лечения", quantity=2
            ),
            ItemGranted(actor_id="fighter", item_id="repair_parts", name="Запчасти", quantity=3),
            ItemGranted(actor_id="fighter", item_id="dm_signet", name="Перстень тени"),
        ]
        await journal.append(events, apply_events(initial, events))
    return _Room(session_id=session_id, initial=initial, journal=journal)


async def _live_state(env: _Env, session_id: uuid.UUID) -> SessionState:
    async with env.sessions() as db:
        row = await db.get(GameSessionRow, session_id)
    assert row is not None
    return SessionState.model_validate(row.state)


async def _set_state(env: _Env, session_id: uuid.UUID, state: SessionState) -> None:
    async with env.sessions() as db:
        row = await db.get(GameSessionRow, session_id)
        assert row is not None
        row.state = state.model_dump(mode="json")
        await db.commit()


def _catalog_ids(response: Any) -> set[str]:
    return {item.id for item in response.items}


# --- Авторский REST: сценарии, предметы, версии ---


async def test_author_list_returns_only_own_scenarios(items_env: _Env) -> None:
    """GET /api/author/scenarios видит только свои сценарии с текущей версией."""
    env = items_env
    endpoint = _endpoint(env.author_router, "/api/author/scenarios", "GET")
    mine = await endpoint(user=env.author, resources=env.resources)
    assert any(
        scenario.slug == env.slug and scenario.current_version_id == env.v2 for scenario in mine
    )
    assert all(scenario.slug != env.slug + "-na" for scenario in mine)
    assert await endpoint(user=env.other, resources=env.resources) == []


async def test_author_get_items_full_report(items_env: _Env) -> None:
    """GET items отдаёт полные предметы, энкаунтеры с лутом и пустой issues-отчёт."""
    env = items_env
    endpoint = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items", "GET")
    response = await endpoint(scenario_id=env.scenario_id, user=env.author, resources=env.resources)
    assert response.version_id == env.v2
    sword = next(item for item in response.items if item.id == "steel_sword")
    assert sword.icon == "sword"
    assert sword.attack is not None
    assert sword.attack.damage == "1d8+2"
    wolf = next(e for e in response.encounters if e.id == "wolf_pack")
    assert wolf.loot == [LootEntry(item_id="wolf_tails", quantity=5, chance=1.0)]
    assert wolf.creatures
    assert response.issues == []


async def test_author_put_publishes_new_version_session_stays_pinned(items_env: _Env) -> None:
    """PUT предмета публикует новую неизменяемую версию; идущая сессия на прежней."""
    env = items_env
    room = await _make_room(env)
    put = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items/{item_id}", "PUT")
    body = authoring.ItemUpdateRequest.model_validate(
        {"name": "Закалённый меч", "icon": "axe", "damage_bonus": 1}
    )
    response = await put(
        scenario_id=env.scenario_id,
        item_id="steel_sword",
        body=body,
        user=env.author,
        resources=env.resources,
    )
    assert response.version_id not in (env.v1, env.v2)
    assert response.item.name == "Закалённый меч"
    assert response.item.icon == "axe"
    assert response.item.damage_bonus == 1
    # Пропущенные поля сохранены.
    assert response.item.slot == "weapon"
    assert response.issues == []

    # Старая версия не изменилась, сессия осталась привязана к ней.
    old = await load_game_content_from_db(env.sessions, env.v2, env.content_path)
    old_sword = next(item for item in old.scenario.items if item.id == "steel_sword")
    assert old_sword.name == "Стальной меч"
    assert old_sword.icon == "sword"
    async with env.sessions() as db:
        pinned = await db.scalar(
            select(GameSessionRow.scenario_version_id).where(GameSessionRow.id == room.session_id)
        )
    assert pinned == env.v2

    # GET items теперь обслуживает отредактированную текущую версию.
    get = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items", "GET")
    after = await get(scenario_id=env.scenario_id, user=env.author, resources=env.resources)
    assert after.version_id == response.version_id
    assert any(item.id == "steel_sword" and item.name == "Закалённый меч" for item in after.items)


async def test_author_access_errors(items_env: _Env) -> None:
    """403 not_scenario_author для чужого и без автора; 404 для сценария и предмета."""
    env = items_env
    get = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items", "GET")
    put = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items/{item_id}", "PUT")
    body = authoring.ItemUpdateRequest.model_validate({"name": "Чужая правка"})
    with pytest.raises(HTTPException) as foreign:
        await put(
            scenario_id=env.scenario_id,
            item_id="steel_sword",
            body=body,
            user=env.other,
            resources=env.resources,
        )
    assert foreign.value.status_code == 403
    assert foreign.value.detail == "not_scenario_author"

    with pytest.raises(HTTPException) as noauthor:
        await get(scenario_id=env.noauthor_scenario_id, user=env.author, resources=env.resources)
    assert noauthor.value.status_code == 403
    assert noauthor.value.detail == "not_scenario_author"

    with pytest.raises(HTTPException) as missing:
        await get(scenario_id=uuid.uuid4(), user=env.author, resources=env.resources)
    assert missing.value.status_code == 404
    assert missing.value.detail == "scenario_not_found"

    with pytest.raises(HTTPException) as unknown_item:
        await put(
            scenario_id=env.scenario_id,
            item_id="no_such_item",
            body=body,
            user=env.author,
            resources=env.resources,
        )
    assert unknown_item.value.status_code == 404
    assert unknown_item.value.detail == "item_not_found"


async def test_author_put_validation_rejects_without_new_version(items_env: _Env) -> None:
    """422 invalid_item и invalid_scenario (issues непусты); версия не публикуется."""
    env = items_env
    put = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items/{item_id}", "PUT")

    async def expect_422(payload: dict[str, Any], code: str) -> list[dict[str, Any]]:
        with pytest.raises(HTTPException) as error:
            await put(
                scenario_id=env.scenario_id,
                item_id="steel_sword",
                body=authoring.ItemUpdateRequest.model_validate(payload),
                user=env.author,
                resources=env.resources,
            )
        assert error.value.status_code == 422
        detail = error.value.detail
        assert detail["code"] == code
        assert detail["issues"]
        return list(detail["issues"])

    # Явный null имени проходит схему запроса, но не схему предмета.
    issues = await expect_422({"name": None}, "invalid_item")
    assert any(issue["code"] == "schema_error" for issue in issues)
    issues = await expect_422({"icon": "spaceship"}, "invalid_scenario")
    assert any(issue["code"] == "unknown_icon" for issue in issues)
    await expect_422({"enhancement": 9}, "invalid_scenario")

    async with env.sessions() as db:
        current = await db.scalar(
            select(ScenarioRow.current_version_id).where(ScenarioRow.id == env.scenario_id)
        )
    assert current == env.v2


async def test_author_get_reports_broken_published_version(items_env: _Env) -> None:
    """Версия с проблемой, опубликованная минуя REST-валидацию: GET показывает отчёт."""
    env = items_env
    broken = env.content.scenario.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"icon": "nonexistent"})
                if item.id == "steel_sword"
                else item
                for item in env.content.scenario.items
            )
        }
    )
    v5 = await publish_scenario_version(
        env.sessions, broken, scenario_id=env.scenario_id, base_version_id=env.v2
    )
    get = _endpoint(env.author_router, "/api/author/scenarios/{scenario_id}/items", "GET")
    response = await get(scenario_id=env.scenario_id, user=env.author, resources=env.resources)
    assert response.version_id == v5
    assert any(
        issue.code == "unknown_icon" and issue.path == "items.steel_sword.icon"
        for issue in response.issues
    )


# --- Каталог предметов сессии ---


async def test_catalog_disabled_or_unpinned_is_empty(items_env: _Env) -> None:
    """items_enabled=false и незакреплённая версия: {items_enabled: false, items: []}."""
    env = items_env
    disabled = env.content.scenario.model_copy(
        update={
            "scenario": env.content.scenario.scenario.model_copy(update={"items_enabled": False})
        }
    )
    v_off = await publish_scenario_version(
        env.sessions, disabled, scenario_id=env.scenario_id, base_version_id=env.v2
    )
    room_off = await _make_room(
        env, version_id=v_off, content=replace(env.content, scenario=disabled)
    )
    endpoint = _endpoint(env.catalog_router, "/api/sessions/{session_id}/item-catalog", "GET")
    response = await endpoint(
        session_id=room_off.session_id, user=env.author, resources=env.resources
    )
    assert response.items_enabled is False
    assert response.items == []

    async with env.sessions() as db:
        row = await db.get(GameSessionRow, room_off.session_id)
        assert row is not None
        row.scenario_version_id = None
        await db.commit()
    response = await endpoint(
        session_id=room_off.session_id, user=env.author, resources=env.resources
    )
    assert response.items_enabled is False
    assert response.items == []


async def test_catalog_visibility_by_seat(items_env: _Env) -> None:
    """dm видит все предметы; player/spectator — public, раскрытые и выданные."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.catalog_router, "/api/sessions/{session_id}/item-catalog", "GET")

    player = await endpoint(session_id=room.session_id, user=env.author, resources=env.resources)
    assert player.items_enabled is True
    player_ids = _catalog_ids(player)
    assert {"steel_sword", "healing_potion", "repair_parts", "dm_signet"} <= player_ids
    assert "dm_orders" not in player_ids
    # Полная механика доступна игроку; владение раскрывает dm-предмет владельцу.
    by_id = {item.id: item for item in player.items}
    assert by_id["steel_sword"].attack is not None
    assert by_id["steel_sword"].attack.damage == "1d8+2"
    assert by_id["healing_potion"].consumable_effect == ItemHeal(type="heal", amount="2d4+2")
    assert by_id["repair_parts"].repair_hp == 5
    assert by_id["dm_signet"].visibility == "dm"
    assert by_id["dm_signet"].icon == "ring"

    dm = await endpoint(session_id=room.session_id, user=env.other, resources=env.resources)
    assert "dm_orders" in _catalog_ids(dm)
    spectator = await endpoint(
        session_id=room.session_id, user=env.watcher, resources=env.resources
    )
    assert _catalog_ids(spectator) == player_ids

    # Раскрытие ведущим открывает предмет игроку без выдачи.
    state = await _live_state(env, room.session_id)
    reveal = Revealed(entity_id="dm_orders", kind="item")
    await room.journal.append([reveal], apply_events(state, [reveal]))
    after = await endpoint(session_id=room.session_id, user=env.author, resources=env.resources)
    assert "dm_orders" in _catalog_ids(after)


async def test_catalog_granted_to_any_actor_is_visible(items_env: _Env) -> None:
    """Предмет в инвентаре любого актёра (даже спутника без места) виден игроку."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.catalog_router, "/api/sessions/{session_id}/item-catalog", "GET")
    before = await endpoint(session_id=room.session_id, user=env.author, resources=env.resources)
    assert "dm_orders" not in _catalog_ids(before)

    state = await _live_state(env, room.session_id)
    grant = ItemGranted(actor_id="rogue", item_id="dm_orders", name="Тайные приказы")
    await room.journal.append([grant], apply_events(state, [grant]))
    after = await endpoint(session_id=room.session_id, user=env.author, resources=env.resources)
    assert "dm_orders" in _catalog_ids(after)


# --- Предметные действия игрока ---


async def test_item_action_success_journal_and_replay(items_env: _Env) -> None:
    """Успех пишет журнал тем же путём BroadcastJournal и меняет state; replay точен."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.turns_router, "/api/sessions/{session_id}/item-action", "POST")

    equip = await endpoint(
        session_id=room.session_id,
        body=ItemActionRequest(action="equip", item_id="steel_sword"),
        user=env.author,
        resources=env.resources,
    )
    assert equip.seq > 0
    assert equip.result.get("equipped")
    state = await _live_state(env, room.session_id)
    assert state.equipment["fighter"]["steel_sword"] == "weapon"
    assert "item:steel_sword" in state.rules.actors["fighter"].attacks

    use = await endpoint(
        session_id=room.session_id,
        body=ItemActionRequest(action="use", item_id="healing_potion"),
        user=env.author,
        resources=env.resources,
    )
    assert use.seq > equip.seq
    state = await _live_state(env, room.session_id)
    assert item_stock(state, "fighter", "healing_potion") == 1

    spend = await endpoint(
        session_id=room.session_id,
        body=ItemActionRequest(
            action="spend", item_id="repair_parts", quantity=2, target_id="fighter"
        ),
        user=env.author,
        resources=env.resources,
    )
    assert spend.result.get("quantity") == 2
    state = await _live_state(env, room.session_id)
    assert item_stock(state, "fighter", "repair_parts") == 1

    async with env.sessions() as db:
        rows = list(
            await db.scalars(
                select(GameEventRow)
                .where(GameEventRow.session_id == room.session_id)
                .order_by(GameEventRow.seq)
            )
        )
    types = [row.type for row in rows]
    assert {"ItemGranted", "ItemEquipped", "ItemUsed", "ItemSpent"} <= set(types)
    replayed = apply_events(
        room.initial, [JOURNAL_EVENT.validate_python(row.payload) for row in rows]
    )
    assert replayed.model_dump(mode="json") == state.model_dump(mode="json")
    # Актёр принудительно из места игрока, не из тела запроса.
    for row in rows:
        if row.type in ("ItemEquipped", "ItemUsed", "ItemSpent"):
            assert row.payload["actor_id"] == "fighter"
    # События ушли в канал комнаты тем же путём BroadcastJournal; замок снят.
    published_types = [message for channel, message in env.redis.published]
    assert published_types
    assert all(channel == f"session:{room.session_id}" for channel, _ in env.redis.published)
    assert await env.redis.exists(lock_key(room.session_id)) == 0


async def test_item_action_role_and_session_gates(items_env: _Env) -> None:
    """Гейты доступа: players_only, no_seat, 404, session_stopped, no_scenario."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.turns_router, "/api/sessions/{session_id}/item-action", "POST")
    body = ItemActionRequest(action="equip", item_id="steel_sword")

    async def expect_error(
        session_id: uuid.UUID, user: CurrentUser, status: int, detail: Any
    ) -> None:
        with pytest.raises(HTTPException) as error:
            await endpoint(session_id=session_id, body=body, user=user, resources=env.resources)
        assert error.value.status_code == status
        assert error.value.detail == detail

    # dm и spectator не имеют предметных действий; пользователь без места — тем более.
    await expect_error(room.session_id, env.other, 403, "players_only")
    await expect_error(room.session_id, env.watcher, 403, "players_only")
    await expect_error(room.session_id, CurrentUser(user_id=uuid.uuid4()), 403, "no_seat")
    await expect_error(uuid.uuid4(), env.author, 404, "session_not_found")

    async with env.sessions() as db:
        row = await db.get(GameSessionRow, room.session_id)
        assert row is not None
        row.status = "stopped"
        await db.commit()
    await expect_error(room.session_id, env.author, 409, {"code": "session_stopped"})

    room_no_version = await _make_room(env, grants=False)
    async with env.sessions() as db:
        row = await db.get(GameSessionRow, room_no_version.session_id)
        assert row is not None
        row.scenario_version_id = None
        await db.commit()
    await expect_error(room_no_version.session_id, env.author, 409, {"code": "no_scenario"})


async def test_item_action_state_gates(items_env: _Env) -> None:
    """Гейты состояния: incapacitated, items_disabled, combat_active, turn_in_progress."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.turns_router, "/api/sessions/{session_id}/item-action", "POST")
    body = ItemActionRequest(action="equip", item_id="steel_sword")

    async def expect_code(status: int, code: str) -> None:
        with pytest.raises(HTTPException) as error:
            await endpoint(
                session_id=room.session_id, body=body, user=env.author, resources=env.resources
            )
        assert error.value.status_code == status
        assert error.value.detail == {"code": code}

    # Нокаут: правим снимок состояния напрямую.
    saved = await _live_state(env, room.session_id)
    dead_actor = saved.rules.actors["fighter"].model_copy(update={"dead": True})
    dead_rules = saved.rules.model_copy(
        update={"actors": {**saved.rules.actors, "fighter": dead_actor}}
    )
    await _set_state(env, room.session_id, saved.model_copy(update={"rules": dead_rules}))
    await expect_code(403, "incapacitated")
    await _set_state(env, room.session_id, saved)

    # Замок комнаты занят ходом.
    await env.redis.set(lock_key(room.session_id), "other-holder", px=30_000)
    with pytest.raises(HTTPException) as locked:
        await endpoint(
            session_id=room.session_id, body=body, user=env.author, resources=env.resources
        )
    assert locked.value.status_code == 409
    assert locked.value.detail == "turn_in_progress"
    await env.redis.delete(lock_key(room.session_id))

    # Идёт бой: предметные действия недоступны.
    combat = CombatStarted(
        sides={"fighter": "party"}, order=("fighter",), initiatives={"fighter": 15}
    )
    await room.journal.append([combat], apply_events(saved, [combat]))
    await expect_code(409, "combat_active")

    # Механика выключена в закреплённой версии.
    disabled = env.content.scenario.model_copy(
        update={
            "scenario": env.content.scenario.scenario.model_copy(update={"items_enabled": False})
        }
    )
    v_off = await publish_scenario_version(
        env.sessions, disabled, scenario_id=env.scenario_id, base_version_id=env.v2
    )
    room_off = await _make_room(
        env, version_id=v_off, content=replace(env.content, scenario=disabled)
    )
    with pytest.raises(HTTPException) as off:
        await endpoint(
            session_id=room_off.session_id, body=body, user=env.author, resources=env.resources
        )
    assert off.value.status_code == 409
    assert off.value.detail == {"code": "items_disabled"}


async def test_item_action_tool_error_and_actor_forcing(items_env: _Env) -> None:
    """ToolCallError → 422 с кодом; actor_id в теле запрещён схемой запроса."""
    env = items_env
    room = await _make_room(env)
    endpoint = _endpoint(env.turns_router, "/api/sessions/{session_id}/item-action", "POST")

    # Клиент не может подменить актёра: extra=forbid на схеме запроса.
    with pytest.raises(ValidationError):
        ItemActionRequest.model_validate(
            {"action": "equip", "item_id": "steel_sword", "actor_id": "rogue"}
        )

    # Меч не расходник: тула отказывает, HTTP-слой отдаёт код ошибки.
    with pytest.raises(HTTPException) as error:
        await endpoint(
            session_id=room.session_id,
            body=ItemActionRequest(action="use", item_id="steel_sword"),
            user=env.author,
            resources=env.resources,
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "item_not_consumable"
    # Отказ не записывает событий и не трогает состояние.
    state = await _live_state(env, room.session_id)
    assert item_stock(state, "fighter", "steel_sword") == 1
    assert state.equipment.get("fighter", {}) == {}
    assert await env.redis.exists(lock_key(room.session_id)) == 0
