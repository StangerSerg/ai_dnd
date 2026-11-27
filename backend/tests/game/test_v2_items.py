"""Юнит-тесты предметного слоя v0.2: модель Item, события инвентаря и replay.

Без БД/сети/LLM: сценарий «five-guardians» грузится из content/, фикстуры предметов —
копии сценария в памяти. Проверяются валидация слотов/эффектов/attack/ammo, применение
ItemGranted/ItemEquipped/ItemUnequipped/ItemEnhanced в game/session.py (состояние
«item:<id>:<enh>» заменяется, а не дублируется) и идентичность replay журнала.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.providers.fake import FakeProvider
from ai_dnd.ai.recorder import MemoryRecorder
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import ItemEnhanced, ItemEquipped, ItemGranted, ItemUnequipped
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import SessionSetup, SessionState, apply_event, apply_events, item_stock
from ai_dnd.game.tools import DmTools, ToolOutcome, item_mechanics
from ai_dnd.rules.engine import RulesEngine, item_condition_id
from ai_dnd.rules.rng import Rng, SeededRng
from ai_dnd.rules.ruleset import Effect, EffectTarget
from ai_dnd.rules.state import AttackProfile
from ai_dnd.scenarios.schemas import MAX_ITEM_ENHANCEMENT, Item, ItemAttack, ItemBuff, ItemHeal
from ai_dnd.scenarios.service import find_scenario_issues

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
START_SCENE = "tavern"

SWORD_ATTACK = ItemAttack(name="Удар меча", bonus=1, damage="1", damage_type="slashing")
AXE_ATTACK = ItemAttack(name="Удар топора", bonus=1, damage="1")
SWORD = Item(id="v2_sword", name="Меч пробы", kind="weapon", slot="weapon", attack=SWORD_ATTACK)
AXE = Item(id="v2_axe", name="Топор пробы", kind="weapon", slot="weapon", attack=AXE_ATTACK)
ARMOR_A = Item(id="v2_armor_a", name="Кольчуга пробы", kind="armor", slot="armor", ac_bonus=1)
ARMOR_B = Item(id="v2_armor_b", name="Латы пробы", kind="armor", slot="armor", ac_bonus=2)
CHARM_A = Item(id="v2_charm_a", name="Оберег А", kind="wearable", slot="other")
CHARM_B = Item(id="v2_charm_b", name="Оберег Б", kind="wearable", slot="other")
OIL = Item(
    id="v2_oil",
    name="Масло меткости",
    kind="consumable",
    consumable_effect=ItemBuff(
        type="buff", effects=(Effect(applies_to=EffectTarget.ATTACKS, bonus=1),), rounds=3
    ),
)


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    """Сценарий «Пять стражей» — тот же, что в смоуках этапа v0.2."""
    return load_game_content(content_dir, SCENARIO, "ru")


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    return setup, new_session_state(content, actors)


def with_items(content: GameContent, *items: Item) -> GameContent:
    """Копия контента с предметами в стартовой сцене (content/ не меняется)."""
    scenario = content.scenario
    ids = tuple(item.id for item in items)
    scenes = tuple(
        scene.model_copy(update={"items": (*scene.items, *ids)})
        if scene.id == START_SCENE
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(update={"items": (*scenario.items, *items), "scenes": scenes})
    return replace(content, scenario=patched)


def make_tools(content: GameContent, setup: SessionSetup, rng: Rng | None = None) -> DmTools:
    engine = RulesEngine(content.ruleset, item_mechanics(content.scenario))
    return DmTools(content, setup, engine, rng if rng is not None else SeededRng(7))


def call(tools: DmTools, state: SessionState, name: str, **arguments: Any) -> ToolOutcome:
    return tools.execute(state, ToolCall(id="test", name=name, arguments=arguments))


def issue_codes(content: GameContent, content_dir: Path, *items: Item) -> list[str]:
    scenario = content.scenario.model_copy(update={"items": (*content.scenario.items, *items)})
    issues = find_scenario_issues(
        scenario,
        content.bestiaries,
        content.ruleset,
        classes=content.classes,
        content_dir=content_dir,
    )
    return [issue.code for issue in issues]


def sword_profile() -> AttackProfile:
    return AttackProfile(name="Удар меча", bonus=1, damage="1", damage_type="slashing")


# --- Валидация модели Item ---


@pytest.mark.parametrize(
    "update",
    [
        {"quantity": 0},
        {"enhancement": -1},
        {"slot": "shield"},
        {"kind": "potion"},
        {"consume_on": "eat"},
        {"repair_hp": 0},
    ],
)
def test_item_schema_rejects_invalid_fields(update: dict[str, Any]) -> None:
    """Слоты, триггеры расхода и количества проверяются самой схемой."""
    with pytest.raises(ValidationError):
        Item(id="v2_probe", name="Проба", **update)


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ((Item(id="v2_bad", name="Пустышка", kind="consumable"),), "consumable_without_effect"),
        (
            (
                Item(
                    id="v2_bad",
                    name="Зелье не туда",
                    kind="consumable",
                    consumable_effect=ItemHeal(type="heal", amount="4"),
                    consume_on="spend",
                ),
            ),
            "effect_requires_use_trigger",
        ),
        (
            (Item(id="v2_bad", name="Лук", ammo="v2_missing", attack=SWORD_ATTACK),),
            "unknown_item",
        ),
        (
            (
                Item(id="v2_bad", name="Лук без атаки", ammo="v2_ammo"),
                Item(id="v2_ammo", name="Стрелы", kind="consumable", consume_on="attack"),
            ),
            "ammo_requires_attack",
        ),
        (
            (
                Item(
                    id="v2_bad",
                    name="Небоеприпас",
                    kind="weapon",
                    consume_on="attack",
                    attack=SWORD_ATTACK,
                ),
            ),
            "attack_ammo_not_consumable",
        ),
        (
            (Item(id="v2_bad", name="Нож без атаки", kind="consumable", consume_on="throw"),),
            "throw_requires_attack",
        ),
        ((Item(id="v2_bad", name="Ремонт не туда", repair_hp=5),), "repair_requires_spend"),
        (
            (Item(id="v2_bad", name="Переточка", enhancement=MAX_ITEM_ENHANCEMENT + 1),),
            "enhancement_over_limit",
        ),
    ],
)
def test_item_issues_reported_by_validator(
    content: GameContent, content_dir: Path, items: tuple[Item, ...], code: str
) -> None:
    """Связки ammo/attack/throw/repair и лимит заточки ловит find_scenario_issues."""
    assert code in issue_codes(content, content_dir, *items)


def test_valid_fixtures_have_no_issues(content: GameContent, content_dir: Path) -> None:
    assert not issue_codes(content, content_dir, SWORD, AXE, ARMOR_A, OIL)


# --- События инвентаря в game/session.py ---


def test_grant_carries_quantity_and_enhancement(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, state = session_pair
    granted = ItemGranted(
        actor_id="fighter", item_id="v2_sword", name="Меч пробы", quantity=3, enhancement=2
    )

    after = apply_event(state, granted)

    entry = after.inventory["fighter"][0]
    assert entry.item_id == "v2_sword"
    assert entry.quantity == 3
    assert entry.enhancement == 2
    assert item_stock(after, "fighter", "v2_sword") == 3


def test_equip_sets_slot_condition_and_attack(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, state = session_pair
    state = apply_event(
        state, ItemGranted(actor_id="fighter", item_id="v2_sword", name="Меч пробы")
    )
    profile = sword_profile()

    after = apply_event(
        state,
        ItemEquipped(
            actor_id="fighter",
            item_id="v2_sword",
            name="Меч пробы",
            slot="weapon",
            attack=profile,
        ),
    )

    assert after.equipment["fighter"] == {"v2_sword": "weapon"}
    actor = after.rules.actor("fighter")
    assert actor.condition(item_condition_id("v2_sword", 0)) is not None
    assert actor.attacks["item:v2_sword"] == profile
    # Владение остаётся в инвентаре.
    assert item_stock(after, "fighter", "v2_sword") == 1


def test_equip_replaces_same_slot_and_attack(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    """Не-other слот вмещает один предмет: прежний снимается вместе с атакой."""
    _, state = session_pair
    state = apply_events(
        state,
        [
            ItemGranted(actor_id="fighter", item_id="v2_sword", name="Меч пробы"),
            ItemGranted(actor_id="fighter", item_id="v2_axe", name="Топор пробы"),
            ItemEquipped(
                actor_id="fighter",
                item_id="v2_sword",
                name="Меч пробы",
                slot="weapon",
                attack=sword_profile(),
            ),
        ],
    )

    after = apply_event(
        state,
        ItemEquipped(
            actor_id="fighter",
            item_id="v2_axe",
            name="Топор пробы",
            slot="weapon",
            attack=AttackProfile(name="Удар топора", bonus=1, damage="1"),
        ),
    )

    assert after.equipment["fighter"] == {"v2_axe": "weapon"}
    actor = after.rules.actor("fighter")
    assert actor.condition(item_condition_id("v2_sword", 0)) is None
    assert actor.condition(item_condition_id("v2_axe", 0)) is not None
    assert "item:v2_sword" not in actor.attacks
    assert "item:v2_axe" in actor.attacks
    # Снятый предмет не исчезает из инвентаря.
    assert item_stock(after, "fighter", "v2_sword") == 1


def test_other_slot_holds_several_items(session_pair: tuple[SessionSetup, SessionState]) -> None:
    _, state = session_pair
    after = apply_events(
        state,
        [
            ItemGranted(actor_id="fighter", item_id="v2_charm_a", name="Оберег А"),
            ItemGranted(actor_id="fighter", item_id="v2_charm_b", name="Оберег Б"),
            ItemEquipped(actor_id="fighter", item_id="v2_charm_a", name="Оберег А", slot="other"),
            ItemEquipped(actor_id="fighter", item_id="v2_charm_b", name="Оберег Б", slot="other"),
        ],
    )

    assert after.equipment["fighter"] == {"v2_charm_a": "other", "v2_charm_b": "other"}


def test_unequip_clears_condition_and_attack(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, state = session_pair
    state = apply_events(
        state,
        [
            ItemGranted(actor_id="fighter", item_id="v2_sword", name="Меч пробы"),
            ItemEquipped(
                actor_id="fighter",
                item_id="v2_sword",
                name="Меч пробы",
                slot="weapon",
                attack=sword_profile(),
            ),
        ],
    )

    after = apply_event(
        state, ItemUnequipped(actor_id="fighter", item_id="v2_sword", name="Меч пробы")
    )

    assert "fighter" not in after.equipment
    actor = after.rules.actor("fighter")
    assert actor.condition(item_condition_id("v2_sword", 0)) is None
    assert "item:v2_sword" not in actor.attacks
    assert item_stock(after, "fighter", "v2_sword") == 1


def test_enhance_replaces_worn_condition(session_pair: tuple[SessionSetup, SessionState]) -> None:
    """Заточка надетого предмета заменяет состояние item:<id>:<enh>, а не добавляет."""
    _, state = session_pair
    state = apply_events(
        state,
        [
            ItemGranted(actor_id="fighter", item_id="v2_sword", name="Меч пробы"),
            ItemEquipped(
                actor_id="fighter",
                item_id="v2_sword",
                name="Меч пробы",
                slot="weapon",
                attack=sword_profile(),
            ),
        ],
    )

    after = apply_event(
        state,
        ItemEnhanced(actor_id="fighter", item_id="v2_sword", name="Меч пробы", enhancement=1),
    )

    assert after.inventory["fighter"][0].enhancement == 1
    actor = after.rules.actor("fighter")
    assert actor.condition(item_condition_id("v2_sword", 0)) is None
    assert actor.condition(item_condition_id("v2_sword", 1)) is not None
    item_conditions = [c.id for c in actor.conditions if c.id.startswith("item:v2_sword:")]
    assert item_conditions == [item_condition_id("v2_sword", 1)]


def test_enhance_without_equipment_touches_only_inventory(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, state = session_pair
    state = apply_event(
        state, ItemGranted(actor_id="fighter", item_id="v2_sword", name="Меч пробы")
    )

    after = apply_event(
        state,
        ItemEnhanced(actor_id="fighter", item_id="v2_sword", name="Меч пробы", enhancement=2),
    )

    assert after.inventory["fighter"][0].enhancement == 2
    assert after.rules == state.rules


def test_equip_without_ownership_is_noop(session_pair: tuple[SessionSetup, SessionState]) -> None:
    _, state = session_pair

    after = apply_event(
        state,
        ItemEquipped(actor_id="fighter", item_id="v2_sword", name="Меч пробы", slot="weapon"),
    )

    assert after == state


def test_item_mechanics_covers_enhancements_and_buffs(content: GameContent) -> None:
    """Карта движка статична: запись на каждую степень 0..MAX плюс ключ бафа."""
    mechanics = item_mechanics(with_items(content, SWORD, ARMOR_A, OIL).scenario)

    for enhancement in range(MAX_ITEM_ENHANCEMENT + 1):
        sword = mechanics[item_condition_id("v2_sword", enhancement)]
        assert sword.weapon is True
        assert sword.enhancement == enhancement
    assert mechanics[item_condition_id("v2_armor_a", 0)].weapon is False
    assert mechanics[item_condition_id("v2_armor_a", 0)].ac_bonus == 1
    oil_effect = OIL.consumable_effect
    assert isinstance(oil_effect, ItemBuff)
    assert mechanics["buff:v2_oil"].effects == oil_effect.effects


# --- Идентичность replay: apply_events(journal) == live state ---


async def test_replay_journal_matches_live_state(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    patched = with_items(content, SWORD, AXE, ARMOR_A, OIL)
    gateway = LLMGateway(
        llm_config, dict.fromkeys(llm_config.providers, FakeProvider()), MemoryRecorder()
    )
    journal = MemoryJournal()
    master = GameMaster(patched, setup, base, gateway, journal, SeededRng(7))
    tools = make_tools(patched, setup)

    for name, arguments in (
        ("grant_item", {"actor_id": "fighter", "item_id": "v2_sword"}),
        ("grant_item", {"actor_id": "fighter", "item_id": "v2_axe"}),
        ("grant_item", {"actor_id": "cleric", "item_id": "v2_armor_a"}),
        ("equip_item", {"actor_id": "fighter", "item_id": "v2_sword"}),
        ("enhance_item", {"actor_id": "fighter", "item_id": "v2_sword"}),
        ("equip_item", {"actor_id": "fighter", "item_id": "v2_axe"}),
        ("equip_item", {"actor_id": "cleric", "item_id": "v2_armor_a"}),
        ("unequip_item", {"actor_id": "cleric", "item_id": "v2_armor_a"}),
    ):
        outcome = call(tools, master.state, name, **arguments)
        assert not outcome.failed, (name, outcome.result)
        await master._commit(outcome.events)

    assert journal.events
    assert apply_events(base, journal.events) == master.state
