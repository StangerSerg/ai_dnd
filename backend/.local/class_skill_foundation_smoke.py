"""Offline-смок Фазы 1 «дата-модель классов и скиллов» (без БД, без web-стека, без LLM).

Запуск из backend: uv run python .local/class_skill_foundation_smoke.py
"""

import tempfile
from pathlib import Path

from ai_dnd.characters.schemas import Character
from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.config import get_settings
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.game.content import load_game_content, load_party
from ai_dnd.game.skills import (
    HealSkill,
    ProtectSkill,
    StealthSkill,
    class_grants,
    load_classes_and_skills,
)

content_dir = get_settings().content_dir
content = load_game_content(content_dir, "five-guardians", "ru")

assert set(content.classes) == {"fighter", "rogue", "cleric", "ranger"}
assert set(content.skills) == {"cure_wounds", "stealth", "protect"}
assert isinstance(content.skills["cure_wounds"], HealSkill)
assert isinstance(content.skills["stealth"], StealthSkill)
assert isinstance(content.skills["protect"], ProtectSkill)
for class_id, character_class in content.classes.items():
    for skill_id in character_class.skills:
        assert skill_id in content.skills, (class_id, skill_id)
print("registries + FK integrity: OK", {k: v.skills for k, v in content.classes.items()})

expected = {
    "cleric": ("cure_wounds",),
    "fighter": ("protect",),
    "rogue": ("stealth",),
    "ranger": (),
}
for class_id, wanted in expected.items():
    character = load_character(content_dir / "characters" / "ru" / f"{class_id}.json")
    sheet = build_sheet(character, content.template)
    granted, cure = class_grants(content.classes, content.skills, class_id, sheet)
    assert granted == wanted, (class_id, granted)
    actor = actor_from_sheet(f"pc_{class_id}", class_id, sheet, skills=granted)
    assert actor.skills == wanted
    if class_id == "cleric":
        assert cure is not None
        assert cure.spell_id == "cure_wounds"
        assert cure.prepared
        assert cure.disciple_of_life
        assert cure.spellcasting_modifier == sheet.computed["spell_mod"]
        assert cure.healing_dice == f"1d8{cure.spellcasting_modifier + 3:+d}"
        print(f"cleric: skills={granted}, capability={cure.model_dump()}")
    else:
        assert cure is None
        print(f"{class_id}: skills={granted}, cure_wounds=None")

# Старый web-персонаж без class_id → пустая выдача, без ошибок.
old = Character(template=content.template.ref, values={"name": "Старый"})
assert old.class_id is None
granted, cure = class_grants(content.classes, content.skills, old.class_id, sheet)
assert granted == ()
assert cure is None
# class_id хранится верхним уровнем data и переживает round-trip.
new = Character(template=content.template.ref, values={}, class_id="cleric")
assert Character.model_validate(new.model_dump(mode="json")).class_id == "cleric"
print("legacy character (class_id=None) graceful: OK")

# Консольный путь load_party: скиллы и capability у мест и актёров.
seats, actors = load_party(content_dir, content, ["cleric", "fighter", "rogue", "ranger"])
for seat, actor in zip(seats, actors, strict=True):
    wanted = expected[seat.actor_id]
    assert seat.skills == wanted
    assert actor.skills == wanted
    assert (seat.cure_wounds is not None) == (seat.actor_id == "cleric")
print("load_party seats/actors grants: OK")

# Целостность: класс с неизвестным скиллом должен падать при загрузке.
with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp)
    (bad / "skills.yaml").write_text("skills:\n  stealth: {kind: stealth}\n", encoding="utf-8")
    (bad / "classes.yaml").write_text("classes:\n  rogue: {skills: [nope]}\n", encoding="utf-8")
    try:
        load_classes_and_skills(bad)
    except ValueError as error:
        print("broken FK rejected: OK —", error)
    else:
        raise AssertionError("broken FK not rejected")

print("SMOKE PASS")
