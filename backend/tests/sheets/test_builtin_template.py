"""Встроенный шаблон «5e SRD (базовый)» из content/sheets."""

from collections.abc import Iterator

from ai_dnd.i18n import LocalizedText
from ai_dnd.sheets.compiled import CompiledTemplate
from ai_dnd.sheets.schemas import ListField, SelectField, SheetTemplate

ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}
SKILL_COUNT = 18


def labels(template: SheetTemplate) -> Iterator[tuple[str, LocalizedText | None]]:
    """Все подписи шаблона с указанием места."""
    yield "name", template.name
    for field in template.fields:
        yield f"fields.{field.id}", field.label
        if isinstance(field, SelectField):
            for option in field.options:
                yield f"fields.{field.id}.options.{option.value}", option.label
        if isinstance(field, ListField):
            for column_id, column in field.item.items():
                yield f"fields.{field.id}.item.{column_id}", column.label
    for index, section in enumerate(template.layout):
        yield f"layout[{index}]", section.section
    for check_id, check in template.bindings.checks.items():
        yield f"bindings.checks.{check_id}", check.label
    for save_id, save in template.bindings.saves.items():
        yield f"bindings.saves.{save_id}", save.label


def test_template_is_valid(srd5_template: CompiledTemplate) -> None:
    assert srd5_template.ref == "srd5-basic@1"


def test_every_label_is_in_russian_and_english(srd5_template: CompiledTemplate) -> None:
    incomplete = [
        place
        for place, label in labels(srd5_template.template)
        if not isinstance(label, dict) or set(label) != {"ru", "en"}
    ]

    assert incomplete == []


def test_every_field_is_shown_exactly_once(srd5_template: CompiledTemplate) -> None:
    template = srd5_template.template
    shown = [field_id for section in template.layout for field_id in section.items]

    assert sorted(shown) == sorted(field.id for field in template.fields)


def test_bindings_cover_abilities_skills_and_saves(srd5_template: CompiledTemplate) -> None:
    bindings = srd5_template.template.bindings

    assert set(bindings.checks) >= ABILITIES
    assert len(bindings.checks) == len(ABILITIES) + SKILL_COUNT
    assert set(bindings.saves) == ABILITIES
    assert (bindings.hp, bindings.ac, bindings.initiative) == ("hp", "ac", "initiative")


def test_armor_types_change_armor_class(srd5_template: CompiledTemplate) -> None:
    def ac(armor_type: str, armor_base: int, dex: int, shield: bool = False) -> int | None:
        return srd5_template.compute(
            {"armor_type": armor_type, "armor_base": armor_base, "dex": dex, "shield": shield}
        ).ac

    # Без доспеха и в лёгком Ловкость идёт целиком, в среднем — не больше +2, в тяжёлом — никак.
    assert ac("none", 10, 18) == 14
    assert ac("light", 12, 18) == 16
    assert ac("medium", 14, 18) == 16
    assert ac("medium", 14, 8) == 13
    assert ac("heavy", 18, 18) == 18
    assert ac("heavy", 18, 8) == 18
    assert ac("heavy", 18, 8, shield=True) == 20


def test_hit_points_follow_hit_die_level_and_constitution(srd5_template: CompiledTemplate) -> None:
    def max_hp(hit_die: int, level: int, con: int, bonus: int = 0) -> int:
        sheet = srd5_template.compute(
            {"hit_die": hit_die, "level": level, "con": con, "hp_bonus": bonus}
        )
        return sheet.resources["hp"].max

    assert max_hp(10, 1, 14) == 12
    assert max_hp(10, 5, 14) == 12 + 4 * 8
    assert max_hp(6, 3, 10) == 6 + 2 * 4
    assert max_hp(8, 3, 14, bonus=3) == 27
    # Даже при очень низком Телосложении уровень даёт хотя бы один хит.
    assert max_hp(6, 4, 1) == 4


def test_spellcasting_ability_can_be_switched(srd5_template: CompiledTemplate) -> None:
    values = {"level": 5, "int": 18, "wis": 14, "cha": 8}

    def spell_stats(ability: str) -> tuple[object, object, object]:
        computed = srd5_template.compute({**values, "spell_ability": ability}).computed
        return computed["spell_mod"], computed["spell_save_dc"], computed["spell_attack"]

    assert spell_stats("int") == (4, 15, 7)
    assert spell_stats("wis") == (2, 13, 5)
    assert spell_stats("cha") == (-1, 10, 2)
    assert spell_stats("none") == (0, 11, 3)
