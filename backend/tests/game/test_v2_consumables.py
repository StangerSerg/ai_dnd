"""Юнит-тесты единой механики расходников v0.2 (подэтап 4b).

Без БД/сети/LLM: бой и эффекты идут через GameMaster/DmTools на копии сценария
«five-guardians» в памяти, броски заданы ScriptedRng. Проверяются consume_on
use/attack/throw/spend, эффекты heal/buff/spell, spend с quantity и target_id
(repair_hp), остаток стека и ошибки нехватки/невалидной цели.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.providers.fake import FakeProvider
from ai_dnd.ai.recorder import MemoryRecorder
from ai_dnd.ai.schemas import ToolCall
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import ItemEquipped, ItemGranted, ItemSpent, ItemUsed, JournalEvent
from ai_dnd.game.journal import MemoryJournal
from ai_dnd.game.master import GameMaster
from ai_dnd.game.session import (
    InventoryItem,
    SessionSetup,
    SessionState,
    affordable_rules,
    apply_event,
    apply_events,
    item_stock,
    spend_item_event,
)
from ai_dnd.game.tools import DmTools, ToolOutcome, item_mechanics
from ai_dnd.rules.actions import AttackAction, EndTurnAction
from ai_dnd.rules.engine import RulesEngine, buff_condition_id, item_condition_id
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import AttackResolved, Healed
from ai_dnd.rules.rng import Rng, ScriptedRng
from ai_dnd.rules.ruleset import Effect, EffectTarget
from ai_dnd.scenarios.schemas import (
    Encounter,
    EncounterGroup,
    Item,
    ItemAttack,
    ItemBuff,
    ItemHeal,
    ItemSpell,
    Statblock,
    StatblockAttack,
)

SCENARIO = "five-guardians"
PARTY = ("fighter", "cleric")
START_SCENE = "tavern"
FIGHT = "v2_fight"
DUMMY = "v2_dummy"

BOW = Item(
    id="v2_bow",
    name="Длинный лук",
    kind="weapon",
    slot="weapon",
    attack=ItemAttack(name="Выстрел из лука", bonus=2, damage="1", damage_type="piercing"),
    ammo="v2_arrows",
)
ARROWS = Item(id="v2_arrows", name="Стрелы", kind="consumable", consume_on="attack", quantity=2)
KNIFE = Item(
    id="v2_knife",
    name="Метательный нож",
    kind="weapon",
    slot="weapon",
    consume_on="throw",
    quantity=2,
    attack=ItemAttack(name="Бросок ножа", bonus=2, damage="1", damage_type="piercing"),
)
SPARES = Item(
    id="v2_spares",
    name="Ремонтный набор",
    kind="consumable",
    consume_on="spend",
    repair_hp=5,
    quantity=3,
)
TORCH = Item(id="v2_torch", name="Факел", kind="consumable", consume_on="spend", quantity=2)
POTION = Item(
    id="v2_potion",
    name="Зелье лечения",
    kind="consumable",
    quantity=2,
    consumable_effect=ItemHeal(type="heal", amount="4"),
)
OIL = Item(
    id="v2_oil",
    name="Масло меткости",
    kind="consumable",
    quantity=2,
    consumable_effect=ItemBuff(
        type="buff", effects=(Effect(applies_to=EffectTarget.ATTACKS, bonus=1),), rounds=3
    ),
)
SCROLL = Item(
    id="v2_scroll",
    name="Свиток огня",
    kind="consumable",
    consumable_effect=ItemSpell(type="spell", spell="Огненный шар"),
)
ALL_ITEMS = (BOW, ARROWS, KNIFE, SPARES, TORCH, POTION, OIL, SCROLL)

# Манекен: высокий КД (промах без урона), много хитов, инициатива заведомо последняя.
DUMMY_BLOCK = Statblock(
    name="Манекен",
    ac=20,
    hp=50,
    initiative=-10,
    checks={},
    attacks={"tap": StatblockAttack(name="Тычок", bonus=0, damage="1")},
)
DUMMY_FIGHT = Encounter(id=FIGHT, title="Пробный бой", creatures=(EncounterGroup(statblock=DUMMY),))


@pytest.fixture(scope="session")
def content(content_dir: Path) -> GameContent:
    base = load_game_content(content_dir, SCENARIO, "ru")
    scenario = base.scenario
    ids = tuple(item.id for item in ALL_ITEMS)
    scenes = tuple(
        scene.model_copy(
            update={"items": (*scene.items, *ids), "encounters": (*scene.encounters, FIGHT)}
        )
        if scene.id == START_SCENE
        else scene
        for scene in scenario.scenes
    )
    patched = scenario.model_copy(
        update={
            "items": (*scenario.items, *ALL_ITEMS),
            "statblocks": {**scenario.statblocks, DUMMY: DUMMY_BLOCK},
            "encounters": (*scenario.encounters, DUMMY_FIGHT),
            "scenes": scenes,
        }
    )
    return replace(base, scenario=patched)


@pytest.fixture(scope="session")
def session_pair(content_dir: Path, content: GameContent) -> tuple[SessionSetup, SessionState]:
    seats, actors = load_party(content_dir, content, PARTY)
    setup = SessionSetup(scenario=SCENARIO, language="ru", seats=tuple(seats))
    return setup, new_session_state(content, actors)


def make_tools(content: GameContent, setup: SessionSetup, rng: Rng) -> DmTools:
    engine = RulesEngine(content.ruleset, item_mechanics(content.scenario))
    return DmTools(content, setup, engine, rng)


def call(tools: DmTools, state: SessionState, name: str, **arguments: Any) -> ToolOutcome:
    return tools.execute(state, ToolCall(id="test", name=name, arguments=arguments))


def make_master(
    content: GameContent,
    setup: SessionSetup,
    state: SessionState,
    rng: Rng,
    llm_config: LLMConfig,
) -> tuple[GameMaster, MemoryJournal]:
    gateway = LLMGateway(
        llm_config, dict.fromkeys(llm_config.providers, FakeProvider()), MemoryRecorder()
    )
    journal = MemoryJournal()
    return GameMaster(content, setup, state, gateway, journal, rng), journal


async def tool(master: GameMaster, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = call(master._tools, master.state, name, **arguments)
    assert not outcome.failed, (name, arguments, outcome.result)
    await master._commit(outcome.events)
    return outcome.result


async def start_fight(master: GameMaster) -> None:
    await tool(master, "start_combat", encounter_id=FIGHT)
    assert master.current_combatant == "fighter"


async def back_to_fighter(master: GameMaster) -> None:
    """Докрутить очередь до воина ходами EndTurn — без бросков и тактики."""
    for _ in range(6):
        current = master.current_combatant
        if not master.in_combat or current == "fighter":
            return
        assert current is not None
        await master.act(EndTurnAction(actor_id=current))
    raise AssertionError("очередь не дошла до воина")


async def attack_and_cycle(master: GameMaster, attack_id: str) -> list[JournalEvent]:
    events = await master.act(
        AttackAction(attacker_id="fighter", target_id=DUMMY, attack=attack_id)
    )
    # Атака тратит действие, но не завершает ход: конец хода шлём отдельно.
    if master.in_combat:
        await master.act(EndTurnAction(actor_id="fighter"))
    await back_to_fighter(master)
    return events


# --- consume_on=attack: боеприпас тратится за попытку, включая промах ---


async def test_attack_spends_ammo_even_on_miss(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    # Инициатива: воин 20, жрец 10, манекен 1; две атаки — натуральная 1 (промах).
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 1, 1]), llm_config)
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_bow")
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_arrows")
    await tool(master, "equip_item", actor_id="fighter", item_id="v2_bow")
    await start_fight(master)

    first = await attack_and_cycle(master, "item:v2_bow")

    assert isinstance(first[0], ItemSpent)
    assert first[0].trigger == "attack"
    assert first[0].item_id == "v2_arrows"
    assert first[0].quantity == 1
    resolved = next(event for event in first if isinstance(event, AttackResolved))
    assert not resolved.outcome.succeeded
    assert first.index(resolved) > 0  # расход записан до событий движка
    assert item_stock(master.state, "fighter", "v2_arrows") == 1

    await attack_and_cycle(master, "item:v2_bow")
    assert item_stock(master.state, "fighter", "v2_arrows") == 0

    # Пустой лук: опции и тактика атаку не предлагают, прямой выбор — ясный отказ.
    options = master.combat_options("fighter")
    assert options
    assert not [
        option
        for option in options
        if isinstance(option, AttackAction) and option.attack == "item:v2_bow"
    ]
    assert "item:v2_bow" not in affordable_rules(master.state, "fighter").actor("fighter").attacks
    before = len(journal.events)
    with pytest.raises(ActionError) as error:
        await master.act(AttackAction(attacker_id="fighter", target_id=DUMMY, attack="item:v2_bow"))
    assert error.value.code == "out_of_ammo"
    assert len(journal.events) == before
    assert apply_events(base, journal.events) == master.state


# --- consume_on=throw: метательный предмет тратит сам себя ---


async def test_throw_spends_self_and_last_unit_unequips(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([20, 10, 1, 1, 1]), llm_config)
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_knife")
    await tool(master, "equip_item", actor_id="fighter", item_id="v2_knife")
    await start_fight(master)

    first = await attack_and_cycle(master, "item:v2_knife")

    assert isinstance(first[0], ItemSpent)
    assert first[0].trigger == "throw"
    assert first[0].item_id == "v2_knife"
    assert item_stock(master.state, "fighter", "v2_knife") == 1
    assert master.state.equipment["fighter"].get("v2_knife") == "weapon"

    await attack_and_cycle(master, "item:v2_knife")

    # Последний нож: предмет ушёл из инвентаря, слот и атака сняты.
    assert item_stock(master.state, "fighter", "v2_knife") == 0
    assert "v2_knife" not in master.state.equipment.get("fighter", {})
    assert "item:v2_knife" not in master.state.rules.actor("fighter").attacks
    with pytest.raises(ActionError) as error:
        await master.act(
            AttackAction(attacker_id="fighter", target_id=DUMMY, attack="item:v2_knife")
        )
    assert error.value.code in {"out_of_ammo", "unknown_attack"}
    assert apply_events(base, journal.events) == master.state


# --- consume_on=spend: quantity, repair_hp и остаток стека ---


async def test_spend_repairs_target_and_caps_at_max(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    cleric_hp = base.rules.actor("cleric").hp
    assert cleric_hp is not None
    cleric_max = cleric_hp.max
    await tool(master, "adjust_resource", actor_id="cleric", resource="hp", delta=-7, reason="рана")
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_spares")

    outcome = call(
        master._tools,
        master.state,
        "spend_item",
        actor_id="fighter",
        item_id="v2_spares",
        quantity=2,
        target_id="cleric",
    )

    assert not outcome.failed, outcome.result
    spent = outcome.events[0]
    assert isinstance(spent, ItemSpent)
    assert spent.trigger == "spend"
    assert spent.quantity == 2
    healed = next(event for event in outcome.events if isinstance(event, Healed))
    assert healed.amount == min(10, 7)
    assert healed.hp_after == cleric_max
    assert outcome.result["remaining"] == 1
    await master._commit(outcome.events)
    assert item_stock(master.state, "fighter", "v2_spares") == 1
    assert apply_events(base, journal.events) == master.state


async def test_plain_spend_is_a_single_item_spent(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_torch")

    outcome = call(
        master._tools,
        master.state,
        "spend_item",
        actor_id="fighter",
        item_id="v2_torch",
        quantity=2,
    )

    assert not outcome.failed
    assert len(outcome.events) == 1
    assert isinstance(outcome.events[0], ItemSpent)
    assert outcome.events[0].quantity == 2
    await master._commit(outcome.events)
    assert item_stock(master.state, "fighter", "v2_torch") == 0
    assert apply_events(base, journal.events) == master.state


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("spend_item", {"item_id": "v2_spares"}, "repair_target_required"),
        ("spend_item", {"item_id": "v2_torch", "target_id": "cleric"}, "item_cannot_repair"),
        ("spend_item", {"item_id": "v2_potion"}, "use_item_required"),
        (
            "spend_item",
            {"item_id": "v2_spares", "quantity": 4, "target_id": "cleric"},
            "insufficient_quantity",
        ),
        ("spend_item", {"item_id": "v2_absent"}, "item_not_in_inventory"),
        ("use_item", {"item_id": "v2_bow"}, "item_not_consumable"),
        ("use_item", {"item_id": "v2_absent"}, "item_not_in_inventory"),
        ("use_item", {"actor_id": "goblin", "item_id": "v2_potion"}, "not_a_party_member"),
    ],
)
async def test_consumable_errors(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
    name: str,
    arguments: dict[str, Any],
    code: str,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    for item_id in ("v2_bow", "v2_spares", "v2_torch", "v2_potion"):
        await tool(master, "grant_item", actor_id="fighter", item_id=item_id)

    before = len(journal.events)
    outcome = call(master._tools, master.state, name, **{"actor_id": "fighter", **arguments})

    assert outcome.result.get("error") == code, outcome.result
    assert not outcome.events
    assert len(journal.events) == before


# --- consume_on=use: heal/buff/spell ---


async def test_use_heal_consumes_one_and_heals(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    await tool(
        master, "adjust_resource", actor_id="fighter", resource="hp", delta=-5, reason="рана"
    )
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_potion")

    outcome = call(master._tools, master.state, "use_item", actor_id="fighter", item_id="v2_potion")

    assert not outcome.failed
    used = outcome.events[0]
    assert isinstance(used, ItemUsed)
    assert used.effect == "heal"
    healed = next(event for event in outcome.events if isinstance(event, Healed))
    assert healed.amount == 4
    assert outcome.result["remaining"] == 1
    await master._commit(outcome.events)
    assert item_stock(master.state, "fighter", "v2_potion") == 1
    assert apply_events(base, journal.events) == master.state


async def test_use_buff_applies_condition_and_does_not_stack(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_oil")

    first = call(master._tools, master.state, "use_item", actor_id="fighter", item_id="v2_oil")
    assert isinstance(first.events[0], ItemUsed)
    assert first.events[0].effect == "buff"
    assert first.events[0].rounds == 3
    await master._commit(first.events)
    second = call(master._tools, master.state, "use_item", actor_id="fighter", item_id="v2_oil")
    await master._commit(second.events)

    actor = master.state.rules.actor("fighter")
    buffs = [
        condition for condition in actor.conditions if condition.id == buff_condition_id("v2_oil")
    ]
    assert len(buffs) == 1
    assert buffs[0].rounds == 3
    assert item_stock(master.state, "fighter", "v2_oil") == 0
    assert apply_events(base, journal.events) == master.state


async def test_use_spell_spends_scroll_without_engine_effects(
    content: GameContent,
    session_pair: tuple[SessionSetup, SessionState],
    llm_config: LLMConfig,
) -> None:
    setup, base = session_pair
    master, journal = make_master(content, setup, base, ScriptedRng([]), llm_config)
    await tool(master, "grant_item", actor_id="fighter", item_id="v2_scroll")

    outcome = call(master._tools, master.state, "use_item", actor_id="fighter", item_id="v2_scroll")

    assert not outcome.failed
    assert len(outcome.events) == 1
    used = outcome.events[0]
    assert isinstance(used, ItemUsed)
    assert used.effect == "spell"
    assert used.spell == "Огненный шар"
    assert outcome.result["spell"] == "Огненный шар"
    await master._commit(outcome.events)
    assert item_stock(master.state, "fighter", "v2_scroll") == 0
    assert apply_events(base, journal.events) == master.state


# --- Остаток стека на уровне состояния (старые журналы с дублями записей) ---


def test_spend_across_duplicate_entries(session_pair: tuple[SessionSetup, SessionState]) -> None:
    _, state = session_pair
    entry = InventoryItem(item_id="v2_spares", name="Ремонтный набор", quantity=1)
    extra = InventoryItem(item_id="v2_spares", name="Ремонтный набор", quantity=2)
    state = state.model_copy(update={"inventory": {"fighter": (entry, extra)}})

    spent = spend_item_event(state, "fighter", "v2_spares", 2, "spend")
    assert spent is not None
    after = apply_event(state, spent)

    assert item_stock(after, "fighter", "v2_spares") == 1
    assert tuple(after.inventory["fighter"]) == (
        InventoryItem(item_id="v2_spares", name="Ремонтный набор", quantity=1),
    )
    assert spend_item_event(after, "fighter", "v2_spares", 2, "spend") is None


def test_last_spent_unit_unequips_thrown_item(
    session_pair: tuple[SessionSetup, SessionState],
) -> None:
    _, state = session_pair
    state = apply_events(
        state,
        [
            ItemGranted(actor_id="fighter", item_id="v2_knife", name="Нож", quantity=1),
            ItemEquipped(actor_id="fighter", item_id="v2_knife", name="Нож", slot="weapon"),
        ],
    )

    after = apply_event(
        state,
        ItemSpent(actor_id="fighter", item_id="v2_knife", name="Нож", quantity=1, trigger="throw"),
    )

    assert item_stock(after, "fighter", "v2_knife") == 0
    assert "fighter" not in after.equipment
    assert after.rules.actor("fighter").condition(item_condition_id("v2_knife", 0)) is None


def test_buff_duration_keeps_the_longer(session_pair: tuple[SessionSetup, SessionState]) -> None:
    """Повторный баф не складывается: остаётся более долгий, None — бессрочный."""
    _, state = session_pair
    state = apply_event(
        state, ItemGranted(actor_id="fighter", item_id="v2_oil", name="Масло", quantity=4)
    )
    buff_id = buff_condition_id("v2_oil")

    def use(rounds: int | None) -> int | None:
        nonlocal state
        state = apply_event(
            state,
            ItemUsed(
                actor_id="fighter", item_id="v2_oil", name="Масло", effect="buff", rounds=rounds
            ),
        )
        condition = state.rules.actor("fighter").condition(buff_id)
        assert condition is not None
        return condition.rounds

    assert use(3) == 3
    assert use(5) == 5
    assert use(2) == 5  # более короткий не заменяет
    assert use(None) is None  # бессрочный заменяет срочный
