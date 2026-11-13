from pathlib import Path
from typing import Any

import pytest

from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.config import Settings
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.rules.engine import RulesEngine
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import Actor, AttackProfile, Resource, Tactics
from ai_dnd.rulesets.service import load_ruleset
from ai_dnd.sheets.compiled import CompiledTemplate, load_template


@pytest.fixture(scope="session")
def content_dir() -> Path:
    """Папка со встроенным контентом (content/ в корне репозитория)."""
    # Без файла .env, чтобы локальные настройки разработчика не влияли на тесты.
    return Settings(_env_file=None).content_dir


@pytest.fixture(scope="session")
def srd5_template(content_dir: Path) -> CompiledTemplate:
    """Встроенный шаблон карточки «5e SRD (базовый)»."""
    return load_template(content_dir / "sheets" / "srd5-basic.json")


@pytest.fixture(scope="session")
def srd5_ruleset(content_dir: Path) -> Ruleset:
    """Встроенный набор правил «5-я редакция (SRD 5.1)»."""
    return load_ruleset(content_dir / "rulesets" / "dnd5e-srd.json")


@pytest.fixture(scope="session")
def engine(srd5_ruleset: Ruleset) -> RulesEngine:
    return RulesEngine(srd5_ruleset)


def _pregenerated(content_dir: Path, template: CompiledTemplate, name: str) -> Actor:
    character = load_character(content_dir / "characters" / "ru" / f"{name}.json")
    sheet = build_sheet(character, template)
    return actor_from_sheet(name, sheet.values["name"], sheet)


@pytest.fixture
def fighter(content_dir: Path, srd5_template: CompiledTemplate) -> Actor:
    """Воин Бранд: КД 19, 28 хитов, длинный меч +5 (1d8+3), Атлетика +5."""
    return _pregenerated(content_dir, srd5_template, "fighter")


@pytest.fixture
def rogue(content_dir: Path, srd5_template: CompiledTemplate) -> Actor:
    """Плутовка Мира: КД 14, 21 хит, Скрытность +7, рапира +5 (1d8+3)."""
    return _pregenerated(content_dir, srd5_template, "rogue")


@pytest.fixture
def cleric(content_dir: Path, srd5_template: CompiledTemplate) -> Actor:
    """Жрец Торгрим: КД 16, 27 хитов, спасбросок Мудрости +5, ячейки заклинаний 4 и 2."""
    return _pregenerated(content_dir, srd5_template, "cleric")


@pytest.fixture
def goblin() -> Actor:
    """Гоблин по SRD 5.1: КД 15, 7 хитов, Скрытность +6, скимитар +4 (1d6+2)."""
    abilities = {"str": -1, "dex": 2, "con": 0, "int": 0, "wis": -1, "cha": -1}
    return Actor(
        id="goblin",
        name="Гоблин",
        kind="creature",
        checks={**abilities, "stealth": 6},
        saves=abilities,
        ac=15,
        initiative=2,
        # Гоблины трусливы: бегут, когда хитов осталось не больше четверти.
        tactics=Tactics(flee_below=0.25),
        resources={"hp": Resource(current=7, max=7)},
        attacks={
            "scimitar": AttackProfile(
                name="Скимитар", bonus=4, damage="1d6+2", damage_type="slashing"
            ),
            "shortbow": AttackProfile(
                name="Короткий лук", bonus=4, damage="1d6+2", damage_type="piercing"
            ),
        },
    )


@pytest.fixture
def template_data() -> dict[str, Any]:
    """Небольшой правильный шаблон со всеми типами полей. Тесты портят его по одному месту."""
    return {
        "id": "mini",
        "version": 3,
        "name": {"ru": "Мини", "en": "Mini"},
        "fields": [
            {"id": "name", "type": "text", "label": "Имя"},
            {
                "id": "level",
                "type": "number",
                "label": {"ru": "Уровень", "en": "Level"},
                "default": 1,
                "min": 1,
                "max": 20,
            },
            {
                "id": "dex",
                "type": "number",
                "label": "Ловкость",
                "default": 10,
                "min": 1,
                "max": 30,
            },
            {
                "id": "dex_mod",
                "type": "computed",
                "label": "Мод. Ловкости",
                "formula": "floor((dex - 10) / 2)",
            },
            {
                "id": "prof",
                "type": "computed",
                "label": "Бонус мастерства",
                "formula": "2 + floor((level - 1) / 4)",
            },
            {"id": "stealth_prof", "type": "checkbox", "label": "Владение Скрытностью"},
            {
                "id": "armor",
                "type": "select",
                "label": "Доспех",
                "options": [
                    {"value": "none", "label": "Нет"},
                    {"value": "heavy", "label": {"ru": "Тяжёлый", "en": "Heavy"}},
                ],
            },
            {"id": "hp", "type": "resource", "label": "Хиты", "max_formula": "8 + (level - 1) * 5"},
            {"id": "luck", "type": "resource", "label": "Удача", "default_max": 3},
            {"id": "bloodied", "type": "computed", "label": "Ранен", "formula": "hp * 2 <= hp_max"},
            {
                "id": "inventory",
                "type": "list",
                "label": "Снаряжение",
                "item": {
                    "name": "text",
                    "qty": {"type": "number", "label": {"ru": "Кол-во", "en": "Qty"}},
                    "magic": "checkbox",
                },
            },
            {"id": "notes", "type": "longtext", "label": "Заметки"},
            {"id": "portrait", "type": "image", "label": "Портрет"},
        ],
        "layout": [
            {"section": "Основное", "cols": 3, "items": ["name", "level", "hp"]},
            {
                "section": {"ru": "Характеристики", "en": "Abilities"},
                "cols": 6,
                "items": ["dex", "dex_mod", "prof"],
            },
        ],
        "bindings": {
            "checks": {
                "stealth": {
                    "label": "Скрытность",
                    "bonus": "dex_mod + (prof if stealth_prof else 0)",
                }
            },
            "saves": {"dex": {"label": "Спасбросок Ловкости", "bonus": "dex_mod"}},
            "hp": "hp",
            "ac": '16 if armor == "heavy" else 10 + dex_mod',
            "initiative": "dex_mod",
        },
    }
