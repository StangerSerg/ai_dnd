"""Дата-модель классов и скиллов.

Скиллы — реестр по id (``content/skills.yaml``), классы ссылаются на них по id
FK-ссылками (``content/classes.yaml``). Персонаж хранит id класса, а не дисплейную
строку, поэтому выдача скиллов не зависит от языка карточки. Механика stealth/protect
появится в Фазе 2; здесь только модель, загрузка и выдача.
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ai_dnd.game.spells import CureWoundsCapability
from ai_dnd.sheets.compiled import ComputedSheet


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HealSkill(_Model):
    kind: Literal["heal"]
    spell_id: Literal["cure_wounds"]
    prepared: bool
    disciple_of_life: bool = False


class StealthSkill(_Model):
    kind: Literal["stealth"]


class ProtectSkill(_Model):
    kind: Literal["protect"]


Skill = Annotated[HealSkill | StealthSkill | ProtectSkill, Field(discriminator="kind")]


class CharacterClass(_Model):
    """Класс: id — ключ реестра (fighter/rogue/cleric/ranger), skills — FK на скиллы."""

    skills: tuple[str, ...] = ()
    label: str | None = None


class _SkillsFile(_Model):
    skills: dict[str, Skill]


class _ClassesFile(_Model):
    classes: dict[str, CharacterClass]


def load_classes_and_skills(
    content_dir: Path,
) -> tuple[dict[str, CharacterClass], dict[str, Skill]]:
    """Реестры классов и скиллов с проверкой целостности FK-ссылок."""
    skills = _SkillsFile.model_validate(_read_yaml(content_dir / "skills.yaml")).skills
    classes = _ClassesFile.model_validate(_read_yaml(content_dir / "classes.yaml")).classes
    for class_id, character_class in classes.items():
        for skill_id in character_class.skills:
            if skill_id not in skills:
                raise ValueError(f"Класс «{class_id}» ссылается на неизвестный скилл «{skill_id}»")
    return classes, skills


def class_grants(
    classes: dict[str, CharacterClass],
    skills: dict[str, Skill],
    class_id: str | None,
    sheet: ComputedSheet,
) -> tuple[tuple[str, ...], CureWoundsCapability | None]:
    """Выдача по классу: id скиллов и runtime-параметры лечения.

    Без класса (старые персонажи) — пустая выдача. Целостность ссылок проверена
    загрузчиком, поэтому скилл по id всегда найден.
    """
    character_class = classes.get(class_id) if class_id is not None else None
    if character_class is None:
        return (), None
    cure_wounds = None
    for skill_id in character_class.skills:
        skill = skills[skill_id]
        if isinstance(skill, HealSkill):
            cure_wounds = _cure_wounds_capability(skill, sheet)
            break
    return character_class.skills, cure_wounds


def _cure_wounds_capability(skill: HealSkill, sheet: ComputedSheet) -> CureWoundsCapability:
    modifier = sheet.computed.get("spell_mod")
    if sheet.values.get("spell_ability") not in ("int", "wis", "cha") or type(modifier) is not int:
        raise ValueError("Для Cure Wounds нужна базовая характеристика заклинаний")
    return CureWoundsCapability(
        spell_id=skill.spell_id,
        prepared=skill.prepared,
        disciple_of_life=skill.disciple_of_life,
        spellcasting_modifier=modifier,
    )


def _read_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
