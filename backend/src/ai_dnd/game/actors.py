"""Участники игры для движка правил.

Движок не знает о карточках, шаблонах и сценариях: он работает с ``Actor`` — готовыми
числами. Здесь в такого участника превращаются посчитанная карточка персонажа
и статблок монстра.
"""

from collections.abc import Collection, Iterable

from ai_dnd.i18n import localize
from ai_dnd.rules.state import HIT_POINTS, Actor, AttackProfile, Resource
from ai_dnd.scenarios.schemas import Bestiary, Scenario, Statblock
from ai_dnd.scenarios.service import resolve_statblock
from ai_dnd.sheets.compiled import ComputedSheet


def attack_id(position: int) -> str:
    """Id атаки по её месту в списке карточки: первая — ``attack_1``."""
    return f"attack_{position}"


def actor_from_sheet(
    actor_id: str, name: str, sheet: ComputedSheet, skills: tuple[str, ...] = ()
) -> Actor:
    """Персонаж игрока как участник игры.

    Бонусы проверок и спасбросков, класс доспеха, ресурсы и атаки берутся из посчитанной
    карточки. Хиты кладутся в ресурс ``hp``, каким бы полем они ни были в шаблоне.
    Если в карточке записан урон, который не разобрать («1d8+»), будет ошибка проверки.
    """
    resources: dict[str, Resource] = {}
    for resource_id, state in sheet.resources.items():
        engine_id = HIT_POINTS if resource_id == sheet.hp_resource else resource_id
        resources[engine_id] = Resource(current=state.current, max=state.max)

    attacks = {
        attack_id(position): AttackProfile(
            name=attack.name,
            bonus=attack.bonus,
            damage=attack.damage,
            damage_type=attack.damage_type,
        )
        for position, attack in enumerate(sheet.attacks, start=1)
    }

    return Actor(
        id=actor_id,
        name=name,
        kind="character",
        checks=dict(sheet.checks),
        saves=dict(sheet.saves),
        ac=sheet.ac,
        initiative=sheet.initiative or 0,
        resources=resources,
        attacks=attacks,
        skills=skills,
    )


def actor_from_statblock(
    actor_id: str, statblock: Statblock, language: str, name: str | None = None
) -> Actor:
    """Существо из статблока как участник игры.

    Названия существа и его атак берутся на языке игры. ``name`` заменяет название
    из статблока: «Жрец Змея» вместо «Фанатик культа».
    """
    return Actor(
        id=actor_id,
        name=name or localize(statblock.name, language),
        kind="creature",
        checks=dict(statblock.checks),
        saves=dict(statblock.saves),
        ac=statblock.ac,
        initiative=statblock.initiative,
        tactics=statblock.tactics,
        resources={HIT_POINTS: Resource(current=statblock.hp, max=statblock.hp)},
        attacks={
            key: AttackProfile(
                name=localize(attack.name, language),
                bonus=attack.bonus,
                damage=attack.damage,
                damage_type=attack.damage_type,
            )
            for key, attack in statblock.attacks.items()
        },
        resistances=statblock.resistances,
        vulnerabilities=statblock.vulnerabilities,
        immunities=statblock.immunities,
    )


def encounter_actors(
    scenario: Scenario,
    encounter_id: str,
    bestiaries: Iterable[Bestiary] = (),
    taken: Collection[str] = (),
) -> list[Actor]:
    """Участники столкновения из сценария, готовые к бою.

    Одинаковые существа получают номера: ``goblin_1`` «Гоблин 1», ``goblin_2`` «Гоблин 2».
    ``taken`` — id, которые уже заняты в игре: новые участники их не повторят.
    """
    encounter = next(item for item in scenario.encounters if item.id == encounter_id)
    language = scenario.scenario.language
    libraries = list(bestiaries)
    used = set(taken)

    actors: list[Actor] = []
    for group in encounter.creatures:
        statblock = resolve_statblock(scenario, group.statblock, libraries)
        if statblock is None:
            raise LookupError(f"Статблок «{group.statblock}» не найден")
        base_id = group.statblock.rsplit(":", 1)[-1]
        base_name = group.name or localize(statblock.name, language)
        for number in range(1, group.count + 1):
            numbered = group.count > 1
            actor_id = f"{base_id}_{number}" if numbered else base_id
            # Тот же монстр уже есть в игре — продолжаем нумерацию.
            suffix = number
            while actor_id in used:
                suffix += 1
                actor_id = f"{base_id}_{suffix}"
            used.add(actor_id)
            name = f"{base_name} {number}" if numbered else base_name
            actors.append(actor_from_statblock(actor_id, statblock, language, name))
    return actors
