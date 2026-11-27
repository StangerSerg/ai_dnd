"""Заклинание, механизированное для свободной игры прототипа."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CURE_WOUNDS_TOOL = "cast_cure_wounds1"
SPELL_SLOT_PREFIX = "spell_slots_"
FIRST_LEVEL_SLOT = f"{SPELL_SLOT_PREFIX}1"


class CureWoundsCapability(BaseModel):
    """Параметры заклинания на момент создания сессии; строится из скилла kind=heal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    spell_id: Literal["cure_wounds"]
    prepared: bool
    disciple_of_life: bool = False
    consumes_slot: bool = True
    spellcasting_modifier: int = Field(ge=-5, le=10)

    @property
    def healing_dice(self) -> str:
        # SRD 5.1: 1d8 + модификатор заклинателя, Ученик жизни добавляет 2 + уровень.
        life_bonus = 2 + 1 if self.disciple_of_life else 0
        return f"1d8{self.spellcasting_modifier + life_bonus:+d}"
