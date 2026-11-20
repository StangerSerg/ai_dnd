"""Детерминированное присутствие NPC в сценах (баг «Хоремхеб в таверне»).

``scene.npcs`` — суперсет по фазам истории: в таверне перечислены Отшельник и
Колесничий, которые живут в других сценах. Авторитетное «где NPC сейчас» —
``state.npc_locations`` (журнал NpcMoved, снимок в PostgreSQL); ``npc.home`` из
сценария — стартовая локация по умолчанию. Эффективная локация = записанное
перемещение поверх home.

Сценарии без home и без записанных перемещений сохраняют старое поведение:
NPC присутствует, если перечислен в ``scene.npcs``.

Статус жизни/смерти — отдельно от присутствия: ``state.npc_status`` (журнал
NpcDied); отсутствие ключа — жив. Мёртвый NPC может присутствовать в сцене
как труп, но не показывается живым в промпте.
"""

from ai_dnd.game.session import SessionState
from ai_dnd.scenarios.schemas import Npc, Scenario, Scene


def effective_location(npc: Npc, state: SessionState) -> str | None:
    """Где NPC сейчас: записанное перемещение поверх home сценария; None — неизвестно."""
    return state.npc_locations.get(npc.id) or npc.home


def is_present(npc: Npc, scene: Scene, state: SessionState) -> bool:
    """NPC «здесь» для промпта: перечислен в сцене и его локация совпадает (или неизвестна)."""
    if npc.id not in scene.npcs:
        return False
    location = effective_location(npc, state)
    return location is None or location == scene.id


def is_dead(npc_id: str, state: SessionState) -> bool:
    """NPC мёртв по авторитетному статусу (журнал NpcDied); отсутствие ключа — жив."""
    return state.npc_status.get(npc_id) == "dead"


def scene_occupancy(scenario: Scenario, state: SessionState) -> dict[str, list[str]]:
    """Обратный индекс эффективной занятости {scene_id: [npc_id, ...]} для Redis-кэша.

    Только NPC с известной локацией (home или перемещение); порядок детерминирован
    порядком npcs сценария. Кэш производный: всегда пересобирается из state+content.
    """
    index: dict[str, list[str]] = {}
    for npc in scenario.npcs:
        location = effective_location(npc, state)
        if location is not None:
            index.setdefault(location, []).append(npc.id)
    return index
