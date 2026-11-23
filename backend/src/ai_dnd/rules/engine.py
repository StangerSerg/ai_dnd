"""Движок правил: ``resolve(state, action, rng) -> события`` (архитектура, разделы 2.2 и 6).

Движок — чистая функция: состояние не меняет, к базе, сети и LLM не обращается, а вся
случайность приходит через ``rng``. Один и тот же seed даёт одни и те же события.

    engine = RulesEngine(ruleset)
    events = engine.resolve(state, CheckAction(actor_id="mira", check="stealth",
                                               difficulty=Difficulty.HARD), rng)
    state = apply_all(state, events)

Если действие выполнить нельзя (нет такого навыка, цель мертва, ресурса не хватает),
движок отвечает ``ActionError`` и ничего не бросает.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, assert_never

from ai_dnd.rules.actions import (
    Action,
    ApplyConditionAction,
    AttackAction,
    CheckAction,
    ContestAction,
    DamageAction,
    EndCombatAction,
    EndTurnAction,
    FleeAction,
    HealAction,
    HideAction,
    PassTimeAction,
    ProtectAction,
    RemoveConditionAction,
    ResourceAction,
    RestAction,
    SaveAction,
    StartCombatAction,
    UseTurnSlotAction,
)
from ai_dnd.rules.apply import apply_all
from ai_dnd.rules.combat import (
    available_actions,
    can_fight,
    combat_end,
    end_combat,
    end_turn,
    flee,
    incapacitated_by,
    require_turn,
    start_combat,
    use_turn_slot,
)
from ai_dnd.rules.dice import (
    ConstantTerm,
    DiceExpression,
    DiceTerm,
    Keep,
    RollMode,
    RollResult,
    Term,
    parse,
    roll,
)
from ai_dnd.rules.errors import ActionError
from ai_dnd.rules.events import (
    ActorDied,
    AttackResolved,
    CheckResolved,
    CombatEnded,
    ConditionApplied,
    ConditionRemoved,
    ContestResolved,
    DamageDealt,
    DamageModifier,
    DiceRolled,
    Event,
    Healed,
    ProtectionEnded,
    ProtectionStarted,
    ResourceChanged,
    Rested,
    TimePassed,
    TurnSlotUsed,
)
from ai_dnd.rules.rng import Rng
from ai_dnd.rules.ruleset import (
    Difficulty,
    Effect,
    EffectTarget,
    Outcome,
    RollBands,
    RollVsTarget,
    Ruleset,
    RulesetError,
    find_ruleset_issues,
)
from ai_dnd.rules.state import HIDDEN, HIT_POINTS, Actor, GameState, Resource, TurnSlot

CheckKind = Literal["check", "save"]


@dataclass(frozen=True, slots=True)
class _CheckRequest:
    kind: CheckKind
    actor_id: str
    subject: str
    difficulty: Difficulty | None
    target: int | None
    mode: RollMode
    visibility: str


@dataclass(frozen=True, slots=True)
class _Modifiers:
    """Что состояния участника добавляют к броску."""

    modes: tuple[RollMode, ...] = ()
    bonuses: tuple[ConstantTerm, ...] = ()
    # Состояние, из-за которого бросок провален без костей.
    auto_failed_by: str | None = None


# Экипировка и бафы предметов живут в actor.conditions как состояния с этими префиксами.
# Их определений нет в наборе правил: движок получает их отдельной картой (решение 18.11 —
# модификаторы экипировки считает движок, карточка не пересчитывается).
ITEM_CONDITION_PREFIX = "item:"
BUFF_CONDITION_PREFIX = "buff:"


def item_condition_id(item_id: str, enhancement: int) -> str:
    """Id состояния надетого предмета; степень заточки — часть id."""
    return f"{ITEM_CONDITION_PREFIX}{item_id}:{enhancement}"


def buff_condition_id(item_id: str) -> str:
    """Id состояния временного бафа одноразового предмета."""
    return f"{BUFF_CONDITION_PREFIX}{item_id}"


def condition_item_id(condition_id: str) -> str | None:
    """Id предмета для состояний экипировки/бафов; None — обычное состояние набора правил."""
    if condition_id.startswith(ITEM_CONDITION_PREFIX):
        return condition_id[len(ITEM_CONDITION_PREFIX) :].rsplit(":", 1)[0]
    if condition_id.startswith(BUFF_CONDITION_PREFIX):
        return condition_id[len(BUFF_CONDITION_PREFIX) :]
    return None


@dataclass(frozen=True, slots=True)
class ItemMechanics:
    """Механика предмета для движка: эффекты бросков плюс скаляры КД и урона.

    Ключ карты — id состояния (``item_condition_id``/``buff_condition_id``); заточка
    зашита в ключ, поэтому карта статична, а степень берётся из состояния участника.
    """

    effects: tuple[Effect, ...] = ()
    ac_bonus: int = 0
    damage_bonus: int = 0
    # Заточка даёт +N к атаке и урону только оружием (slot == weapon).
    weapon: bool = False
    enhancement: int = 0


class RulesEngine:
    def __init__(self, ruleset: Ruleset, items: Mapping[str, ItemMechanics] | None = None) -> None:
        issues = find_ruleset_issues(ruleset)
        if issues:
            raise RulesetError(issues)
        self.ruleset = ruleset
        self._items: Mapping[str, ItemMechanics] = dict(items) if items else {}
        self._check_dice: tuple[Term, ...] = parse(ruleset.resolution.dice).terms
        self._attack_dice: tuple[Term, ...] = parse(ruleset.combat.attack_dice).terms

    def resolve(self, state: GameState, action: Action, rng: Rng) -> list[Event]:
        """События, к которым приводит действие. Состояние не меняется."""
        events = self._dispatch(state, action, rng)
        events.extend(self._fallen_protectors(state, events, action.visibility))
        if state.combat is not None and not any(isinstance(e, CombatEnded) for e in events):
            # Бой заканчивается сам, как только на ногах осталась одна сторона —
            # от удара, ловушки или решения ведущего.
            ended = combat_end(apply_all(state, events), action.visibility)
            if ended is not None:
                events.append(ended)
        return events

    def _fallen_protectors(
        self, state: GameState, events: list[Event], visibility: str
    ) -> list[Event]:
        """Защитник погиб или потерял сознание от любого урона — прикрытие спадает."""
        fallen: list[str] = []
        for event in events:
            if isinstance(event, ActorDied):
                fallen.append(event.actor_id)
            elif isinstance(event, DamageDealt) and event.hp_after == 0:
                fallen.append(event.target_id)
        already_ended = {
            (event.protector_id, event.target_id)
            for event in events
            if isinstance(event, ProtectionEnded)
        }
        extra: list[Event] = []
        for protector_id in dict.fromkeys(fallen):
            extra.extend(
                ProtectionEnded(
                    visibility=visibility,
                    protector_id=protector_id,
                    target_id=ward.id,
                    reason="protector_fell",
                )
                for ward in state.actors.values()
                if ward.protected_by == protector_id
                and (protector_id, ward.id) not in already_ended
            )
        return extra

    def available_actions(self, state: GameState, actor_id: str) -> list[Action]:
        """Что участник боя может сделать прямо сейчас. Вне своего хода — ничего."""
        return available_actions(self.ruleset, state, actor_id)

    def _dispatch(self, state: GameState, action: Action, rng: Rng) -> list[Event]:
        match action:
            case CheckAction():
                return self._check(
                    state,
                    _CheckRequest(
                        "check",
                        action.actor_id,
                        action.check,
                        action.difficulty,
                        action.target,
                        action.mode,
                        action.visibility,
                    ),
                    rng,
                )
            case SaveAction():
                return self._check(
                    state,
                    _CheckRequest(
                        "save",
                        action.actor_id,
                        action.save,
                        action.difficulty,
                        action.target,
                        action.mode,
                        action.visibility,
                    ),
                    rng,
                )
            case ContestAction():
                return self._contest(state, action, rng)
            case AttackAction():
                return self._attack(state, action, rng)
            case DamageAction():
                target = _alive(state.actor(action.target_id))
                return self._damage(
                    target,
                    parse(action.damage),
                    action.damage_type,
                    action.source_id,
                    action.visibility,
                    rng,
                )
            case HealAction():
                return self._heal(state, action, rng)
            case ResourceAction():
                return self._change_resource(state, action)
            case ApplyConditionAction():
                actor = _alive(state.actor(action.actor_id))
                if action.condition not in self.ruleset.conditions:
                    raise ActionError("unknown_condition", condition=action.condition)
                return [
                    ConditionApplied(
                        visibility=action.visibility,
                        actor_id=actor.id,
                        condition=action.condition,
                        rounds=action.rounds,
                    )
                ]
            case RemoveConditionAction():
                actor = state.actor(action.actor_id)
                if actor.condition(action.condition) is None:
                    raise ActionError(
                        "condition_not_active", actor_id=actor.id, condition=action.condition
                    )
                return [
                    ConditionRemoved(
                        visibility=action.visibility,
                        actor_id=actor.id,
                        condition=action.condition,
                        reason="removed",
                    )
                ]
            case PassTimeAction():
                return self._pass_time(state, action)
            case RestAction():
                return self._rest(state, action)
            case StartCombatAction():
                return start_combat(self.ruleset, state, action, rng)
            case EndTurnAction():
                return end_turn(self.ruleset, state, action)
            case UseTurnSlotAction():
                return use_turn_slot(state, action)
            case FleeAction():
                return flee(self.ruleset, state, action)
            case HideAction():
                return self._hide(state, action)
            case ProtectAction():
                return self._protect(state, action)
            case EndCombatAction():
                return end_combat(state, action)
            case _:
                assert_never(action)

    def run(self, state: GameState, action: Action, rng: Rng) -> tuple[list[Event], GameState]:
        """Выполнить действие: события и состояние после них."""
        events = self.resolve(state, action, rng)
        return events, apply_all(state, events)

    # --- Проверки, спасброски, состязания ---

    def _check(self, state: GameState, request: _CheckRequest, rng: Rng) -> list[Event]:
        actor = _alive(state.actor(request.actor_id))
        if request.kind == "check":
            # Спасбросок делают и без сознания, а вот проверку — нет.
            self._ensure_can_act(actor)
        bonus = self._bonus(actor, request.kind, request.subject)
        target, difficulty_bonus = self._difficulty(request.difficulty, request.target)

        effect_target = EffectTarget.CHECKS if request.kind == "check" else EffectTarget.SAVES
        modifiers = self._modifiers(actor, effect_target, request.subject)
        mode = _combine_modes([request.mode, *modifiers.modes])

        if modifiers.auto_failed_by is not None:
            return [
                CheckResolved(
                    visibility=request.visibility,
                    actor_id=actor.id,
                    kind=request.kind,
                    check=request.subject,
                    outcome=Outcome.FAILURE,
                    total=None,
                    natural=None,
                    target=target,
                    difficulty=request.difficulty,
                    mode=mode,
                    auto_failed_by=modifiers.auto_failed_by,
                )
            ]

        terms: list[Term] = [
            *self._check_dice,
            ConstantTerm.signed(bonus, label=request.subject),
            *modifiers.bonuses,
        ]
        if difficulty_bonus:
            terms.append(ConstantTerm.signed(difficulty_bonus, label="difficulty"))
        result = roll(DiceExpression(terms=tuple(terms)), rng, mode=mode)

        return [
            DiceRolled(
                visibility=request.visibility, actor_id=actor.id, reason=request.kind, roll=result
            ),
            CheckResolved(
                visibility=request.visibility,
                actor_id=actor.id,
                kind=request.kind,
                check=request.subject,
                outcome=self._outcome(result, target),
                total=result.total,
                natural=result.natural,
                target=target,
                difficulty=request.difficulty,
                mode=mode,
            ),
        ]

    def _contest(self, state: GameState, action: ContestAction, rng: Rng) -> list[Event]:
        sides = (
            (action.actor_id, action.check, action.mode),
            (action.opponent_id, action.opponent_check, action.opponent_mode),
        )
        # Сначала проверяем обе стороны и только потом бросаем: если состязание
        # невозможно, не должна быть брошена ни одна кость.
        prepared: list[tuple[Actor, DiceExpression, RollMode] | None] = []
        for actor_id, check, requested_mode in sides:
            actor = _alive(state.actor(actor_id))
            self._ensure_can_act(actor)
            bonus = self._bonus(actor, "check", check)
            modifiers = self._modifiers(actor, EffectTarget.CHECKS, check)
            if modifiers.auto_failed_by is not None:
                prepared.append(None)
                continue
            terms = (*self._check_dice, ConstantTerm.signed(bonus, label=check), *modifiers.bonuses)
            mode = _combine_modes([requested_mode, *modifiers.modes])
            prepared.append((actor, DiceExpression(terms=terms), mode))

        events: list[Event] = []
        totals: list[int | None] = []
        for side in prepared:
            if side is None:
                totals.append(None)
                continue
            actor, expression, mode = side
            result = roll(expression, rng, mode=mode)
            events.append(
                DiceRolled(
                    visibility=action.visibility, actor_id=actor.id, reason="contest", roll=result
                )
            )
            totals.append(result.total)

        total, opponent_total = totals
        # Кто провалил проверку без броска, тот проиграл. При равенстве — ничья.
        winner_id: str | None = None
        if total is not None and (opponent_total is None or total > opponent_total):
            winner_id = action.actor_id
        elif opponent_total is not None and (total is None or opponent_total > total):
            winner_id = action.opponent_id

        events.append(
            ContestResolved(
                visibility=action.visibility,
                actor_id=action.actor_id,
                check=action.check,
                total=total,
                opponent_id=action.opponent_id,
                opponent_check=action.opponent_check,
                opponent_total=opponent_total,
                winner_id=winner_id,
            )
        )
        return events

    def _bonus(self, actor: Actor, kind: CheckKind, subject: str) -> int:
        if kind == "save":
            if subject not in self.ruleset.saves:
                raise ActionError("unknown_save", save=subject)
            if subject not in actor.saves:
                raise ActionError("save_not_available", actor_id=actor.id, save=subject)
            return actor.saves[subject]

        definition = self.ruleset.checks.get(subject)
        if definition is None:
            raise ActionError("unknown_check", check=subject)
        if subject in actor.checks:
            return actor.checks[subject]
        if definition.ability is not None and definition.ability in actor.checks:
            # У участника нет своего бонуса для навыка — это проверка характеристики.
            return actor.checks[definition.ability]
        raise ActionError("check_not_available", actor_id=actor.id, check=subject)

    def _difficulty(
        self, difficulty: Difficulty | None, target: int | None
    ) -> tuple[int | None, int]:
        """Число, с которым сравнивается итог, и поправка к броску — что из этого
        использует способ разрешения проверок."""
        resolution = self.ruleset.resolution
        if isinstance(resolution, RollVsTarget):
            if target is not None:
                return target, 0
            if difficulty is None:
                raise ActionError("missing_difficulty")
            return self.ruleset.difficulty[difficulty], 0

        if target is not None:
            # В системе с диапазонами точной сложности нет — только поправка по уровню.
            raise ActionError("target_not_supported", resolution=resolution.type)
        return None, 0 if difficulty is None else self.ruleset.difficulty[difficulty]

    def _outcome(self, result: RollResult, target: int | None) -> Outcome:
        resolution = self.ruleset.resolution
        match resolution:
            case RollVsTarget():
                if target is None:
                    raise ActionError("missing_difficulty")
                natural = result.natural
                if natural is not None:
                    success_from = resolution.critical_success
                    failure_until = resolution.critical_failure
                    if success_from is not None and natural >= success_from:
                        return Outcome.CRITICAL_SUCCESS
                    if failure_until is not None and natural <= failure_until:
                        return Outcome.CRITICAL_FAILURE
                return Outcome.SUCCESS if result.total >= target else Outcome.FAILURE
            case RollBands():
                for band in sorted(resolution.bands, key=lambda band: band.min, reverse=True):
                    if result.total >= band.min:
                        return band.outcome
                return resolution.below
            case _:
                assert_never(resolution)

    # --- Атаки, урон, лечение ---

    def _attack(self, state: GameState, action: AttackAction, rng: Rng) -> list[Event]:
        combat = self.ruleset.combat
        if not combat.enabled:
            raise ActionError("combat_disabled")
        attacker = _alive(state.actor(action.attacker_id))
        self._ensure_can_act(attacker)
        target = _alive(state.actor(action.target_id))
        profile = attacker.attacks.get(action.attack)
        if profile is None:
            raise ActionError("unknown_attack", actor_id=attacker.id, attack=action.attack)

        events: list[Event] = []
        # Прикрытую цель защищает другой участник: бросок идёт по его КД,
        # и урон получает он. Выбывший защитник — прикрытие спадает на месте.
        struck = target
        if target.protected_by is not None:
            protector = state.actors.get(target.protected_by)
            if protector is not None and self._can_guard(state, protector):
                struck = protector
            else:
                events.append(
                    ProtectionEnded(
                        visibility=action.visibility,
                        protector_id=target.protected_by,
                        target_id=target.id,
                        reason="protector_fell",
                    )
                )
        if struck.ac is None:
            raise ActionError("no_armor_class", actor_id=struck.id)
        target_ac = struck.ac + self._item_ac(struck)
        if profile.damage is not None:
            # До броска атаки: если урон нанести нельзя, не должна быть брошена ни одна кость.
            self._ensure_can_be_damaged(struck, profile.damage_type)

        # В бою атака стоит части хода и возможна только в свой ход. «free» — исключение
        # для атак вне очереди. Участников вне боя это не касается.
        paid_with: TurnSlot | None = None
        if state.combat is not None:
            if target.id in state.combat.fled:
                raise ActionError("actor_fled", actor_id=target.id)
            if action.slot != "free" and attacker.id in state.combat.sides:
                require_turn(state, attacker.id, action.slot)
                paid_with = action.slot

        own = self._modifiers(attacker, EffectTarget.ATTACKS, None)
        against = self._modifiers(struck, EffectTarget.ATTACKS_AGAINST, None)
        mode = _combine_modes([action.mode, *own.modes, *against.modes])
        terms = (
            *self._attack_dice,
            ConstantTerm.signed(profile.bonus, label=action.attack),
            *own.bonuses,
            *against.bonuses,
        )
        result = roll(DiceExpression(terms=terms), rng, mode=mode)

        natural = result.natural
        if (
            natural is not None
            and combat.automatic_miss is not None
            and natural <= combat.automatic_miss
        ):
            outcome = Outcome.CRITICAL_FAILURE
        elif (
            natural is not None
            and combat.critical_hit is not None
            and natural >= combat.critical_hit
        ):
            outcome = Outcome.CRITICAL_SUCCESS
        else:
            outcome = Outcome.SUCCESS if result.total >= target_ac else Outcome.FAILURE

        if paid_with is not None:
            events.append(
                TurnSlotUsed(visibility=action.visibility, actor_id=attacker.id, slot=paid_with)
            )
        events += [
            DiceRolled(
                visibility=action.visibility, actor_id=attacker.id, reason="attack", roll=result
            ),
            AttackResolved(
                visibility=action.visibility,
                attacker_id=attacker.id,
                target_id=target.id,
                attack=action.attack,
                attack_name=profile.name,
                outcome=outcome,
                total=result.total,
                natural=natural,
                target_ac=target_ac,
                mode=mode,
                redirected_to_id=struck.id if struck.id != target.id else None,
            ),
        ]
        if outcome.succeeded and profile.damage is not None:
            damage = parse(profile.damage)
            equipment_bonus = self._item_damage(attacker)
            if equipment_bonus:
                damage = DiceExpression(
                    terms=(*damage.terms, ConstantTerm.signed(equipment_bonus, label="equipment"))
                )
            if outcome is Outcome.CRITICAL_SUCCESS and combat.critical_damage == "double_dice":
                damage = _double_dice(damage)
            events.extend(
                self._damage(
                    struck, damage, profile.damage_type, attacker.id, action.visibility, rng
                )
            )
        if attacker.condition(HIDDEN) is not None:
            # Своя атака выдаёт скрытого — после броска, чтобы он ещё успел дать преимущество.
            events.append(
                ConditionRemoved(
                    visibility=action.visibility,
                    actor_id=attacker.id,
                    condition=HIDDEN,
                    reason="attacked",
                )
            )
        return events

    def _can_guard(self, state: GameState, protector: Actor) -> bool:
        """Защитник способен принять удар: на ногах и в сознании."""
        return can_fight(state, protector.id) and incapacitated_by(self.ruleset, protector) is None

    def _hide(self, state: GameState, action: HideAction) -> list[Event]:
        actor = _alive(state.actor(action.actor_id))
        self._ensure_can_act(actor)
        if "stealth" not in actor.skills:
            raise ActionError("skill_not_available", actor_id=actor.id, skill="stealth")
        if HIDDEN not in self.ruleset.conditions:
            raise ActionError("unknown_condition", condition=HIDDEN)
        if actor.condition(HIDDEN) is not None:
            raise ActionError("already_hidden", actor_id=actor.id)
        events: list[Event] = []
        if state.combat is not None and action.slot != "free" and actor.id in state.combat.sides:
            require_turn(state, actor.id, action.slot)
            events.append(
                TurnSlotUsed(visibility=action.visibility, actor_id=actor.id, slot=action.slot)
            )
        events.append(
            ConditionApplied(
                visibility=action.visibility,
                actor_id=actor.id,
                condition=HIDDEN,
                rounds=None,
            )
        )
        return events

    def _protect(self, state: GameState, action: ProtectAction) -> list[Event]:
        if not self.ruleset.combat.enabled:
            raise ActionError("combat_disabled")
        protector = _alive(state.actor(action.actor_id))
        self._ensure_can_act(protector)
        if "protect" not in protector.skills:
            raise ActionError("skill_not_available", actor_id=protector.id, skill="protect")
        combat = require_turn(state, protector.id, action.slot)
        target = _alive(state.actor(action.target_id))
        if target.id == protector.id:
            raise ActionError("cannot_protect_self", actor_id=protector.id)
        if combat.sides.get(target.id) != combat.sides[protector.id]:
            raise ActionError("not_an_ally", actor_id=protector.id, target_id=target.id)
        if not can_fight(state, target.id) or incapacitated_by(self.ruleset, target) is not None:
            raise ActionError("ally_cannot_be_protected", target_id=target.id)
        if target.protected_by == protector.id:
            raise ActionError("already_protected", actor_id=protector.id, target_id=target.id)
        return [
            TurnSlotUsed(visibility=action.visibility, actor_id=protector.id, slot=action.slot),
            ProtectionStarted(
                visibility=action.visibility, protector_id=protector.id, target_id=target.id
            ),
        ]

    def _ensure_can_be_damaged(self, target: Actor, damage_type: str | None) -> Resource:
        """Хиты цели — или отказ, если хитов нет либо тип урона набору правил неизвестен."""
        hp = target.hp
        if hp is None:
            raise ActionError("no_hit_points", actor_id=target.id)
        known_types = self.ruleset.damage_types
        if damage_type is not None and known_types and damage_type not in known_types:
            raise ActionError("unknown_damage_type", damage_type=damage_type)
        return hp

    def _damage(
        self,
        target: Actor,
        damage: DiceExpression,
        damage_type: str | None,
        source_id: str | None,
        visibility: str,
        rng: Rng,
    ) -> list[Event]:
        hp = self._ensure_can_be_damaged(target, damage_type)

        result = roll(damage, rng, mode=RollMode.NORMAL)
        events: list[Event] = []
        if result.natural is not None:
            events.append(
                DiceRolled(visibility=visibility, actor_id=source_id, reason="damage", roll=result)
            )

        rolled = max(0, result.total)
        amount, modifiers = _adjust_damage(target, rolled, damage_type)
        hp_after = max(0, hp.current - amount)
        events.append(
            DamageDealt(
                visibility=visibility,
                target_id=target.id,
                source_id=source_id,
                amount=amount,
                rolled=rolled,
                damage_type=damage_type,
                modifiers=modifiers,
                hp_after=hp_after,
            )
        )

        if hp.current > 0 and hp_after == 0:
            zero_hp = self.ruleset.combat.zero_hp
            if target.kind == "creature" and zero_hp.creatures_die:
                events.append(ActorDied(visibility=visibility, actor_id=target.id))
            elif zero_hp.condition is not None and target.condition(zero_hp.condition) is None:
                events.append(
                    ConditionApplied(
                        visibility=visibility,
                        actor_id=target.id,
                        condition=zero_hp.condition,
                        rounds=None,
                    )
                )
        return events

    def _heal(self, state: GameState, action: HealAction, rng: Rng) -> list[Event]:
        target = _alive(state.actor(action.target_id))
        if target.hp is None:
            raise ActionError("no_hit_points", actor_id=target.id)

        result = roll(parse(action.amount), rng, mode=RollMode.NORMAL)
        events: list[Event] = []
        if result.natural is not None:
            events.append(
                DiceRolled(
                    visibility=action.visibility,
                    actor_id=action.source_id,
                    reason="healing",
                    roll=result,
                )
            )
        events.extend(
            self._restore_hit_points(
                target, max(0, result.total), action.source_id, action.visibility
            )
        )
        return events

    def _restore_hit_points(
        self, target: Actor, rolled: int, source_id: str | None, visibility: str
    ) -> list[Event]:
        hp = target.hp
        if hp is None:
            return []
        amount = min(rolled, hp.max - hp.current)
        events: list[Event] = [
            Healed(
                visibility=visibility,
                target_id=target.id,
                source_id=source_id,
                amount=amount,
                rolled=rolled,
                hp_after=hp.current + amount,
            )
        ]
        zero_hp_condition = self.ruleset.combat.zero_hp.condition
        if (
            hp.current == 0
            and amount > 0
            and zero_hp_condition is not None
            and target.condition(zero_hp_condition) is not None
        ):
            # Хиты поднялись выше нуля — участник приходит в себя.
            events.append(
                ConditionRemoved(
                    visibility=visibility,
                    actor_id=target.id,
                    condition=zero_hp_condition,
                    reason="healed",
                )
            )
        return events

    # --- Ресурсы, время, отдых ---

    def _change_resource(self, state: GameState, action: ResourceAction) -> list[Event]:
        actor = _alive(state.actor(action.actor_id))
        if action.resource == HIT_POINTS:
            # Иначе обошли бы сопротивления и правило «0 хитов».
            raise ActionError("hit_points_need_damage_or_healing")
        resource = actor.resources.get(action.resource)
        if resource is None:
            raise ActionError("unknown_resource", actor_id=actor.id, resource=action.resource)

        current = resource.current + action.delta
        if current < 0:
            raise ActionError(
                "not_enough_resource",
                actor_id=actor.id,
                resource=action.resource,
                current=resource.current,
                needed=-action.delta,
            )
        current = min(current, resource.max)
        if current == resource.current:
            return []
        return [
            ResourceChanged(
                visibility=action.visibility,
                actor_id=actor.id,
                resource=action.resource,
                delta=current - resource.current,
                current=current,
            )
        ]

    def _pass_time(self, state: GameState, action: PassTimeAction) -> list[Event]:
        events: list[Event] = [TimePassed(visibility=action.visibility, rounds=action.rounds)]
        for actor in state.actors.values():
            events.extend(
                ConditionRemoved(
                    visibility=action.visibility,
                    actor_id=actor.id,
                    condition=item.id,
                    reason="expired",
                )
                for item in actor.conditions
                if item.rounds is not None and item.rounds <= action.rounds
            )
        return events

    def _rest(self, state: GameState, action: RestAction) -> list[Event]:
        actor_ids = action.actor_ids or tuple(
            actor.id for actor in state.actors.values() if not actor.dead
        )
        events: list[Event] = [
            Rested(visibility=action.visibility, rest=action.rest, actor_ids=actor_ids)
        ]
        for actor_id in actor_ids:
            actor = _alive(state.actor(actor_id))
            for resource_id, resource in actor.resources.items():
                definition = self.ruleset.resources.get(resource_id)
                if definition is None or definition.recovery is None:
                    continue
                recovery = definition.recovery
                if recovery.rest == "long" and action.rest == "short":
                    continue
                missing = resource.max - resource.current
                gain = (
                    missing
                    if recovery.amount == "full"
                    else min(missing, max(1, resource.max // 2))
                )
                if gain <= 0:
                    continue
                if resource_id == HIT_POINTS:
                    events.extend(self._restore_hit_points(actor, gain, None, action.visibility))
                else:
                    events.append(
                        ResourceChanged(
                            visibility=action.visibility,
                            actor_id=actor.id,
                            resource=resource_id,
                            delta=gain,
                            current=resource.current + gain,
                        )
                    )
        return events

    # --- Состояния ---

    def _modifiers(self, actor: Actor, target: EffectTarget, subject: str | None) -> _Modifiers:
        modes: list[RollMode] = []
        bonuses: list[ConstantTerm] = []
        auto_failed_by: str | None = None
        for active in actor.conditions:
            definition = self.ruleset.conditions.get(active.id)
            if definition is not None:
                effects = definition.effects
                label = active.id
            else:
                mechanics = self._items.get(active.id)
                if mechanics is None:
                    continue
                effects = mechanics.effects
                label = condition_item_id(active.id) or active.id
                if mechanics.weapon and mechanics.enhancement and target is EffectTarget.ATTACKS:
                    bonuses.append(
                        ConstantTerm.signed(
                            mechanics.enhancement, label=f"{label}+{mechanics.enhancement}"
                        )
                    )
            for effect in effects:
                if effect.applies_to is not target:
                    continue
                if effect.only and subject not in effect.only:
                    continue
                if effect.mode is not None:
                    modes.append(effect.mode)
                if effect.bonus:
                    bonuses.append(ConstantTerm.signed(effect.bonus, label=label))
                if effect.auto_fail and auto_failed_by is None:
                    auto_failed_by = active.id
        return _Modifiers(tuple(modes), tuple(bonuses), auto_failed_by)

    def _item_ac(self, actor: Actor) -> int:
        """Надбавка КД от надетых предметов (бафы КД не дают)."""
        total = 0
        for active in actor.conditions:
            mechanics = self._items.get(active.id)
            if mechanics is not None:
                total += mechanics.ac_bonus
        return total

    def _item_damage(self, actor: Actor) -> int:
        """Плоская надбавка урона от экипировки: damage_bonus плюс заточка оружия."""
        total = 0
        for active in actor.conditions:
            mechanics = self._items.get(active.id)
            if mechanics is not None:
                total += mechanics.damage_bonus + (mechanics.enhancement if mechanics.weapon else 0)
        return total

    def _ensure_can_act(self, actor: Actor) -> None:
        condition = incapacitated_by(self.ruleset, actor)
        if condition is not None:
            raise ActionError("actor_incapacitated", actor_id=actor.id, condition=condition)


def _alive(actor: Actor) -> Actor:
    if actor.dead:
        raise ActionError("actor_dead", actor_id=actor.id)
    return actor


def _combine_modes(modes: Iterable[RollMode]) -> RollMode:
    """Преимущество и помеха не складываются: если есть и то и другое — обычный бросок."""
    present = set(modes)
    advantage = RollMode.ADVANTAGE in present
    disadvantage = RollMode.DISADVANTAGE in present
    if advantage == disadvantage:
        return RollMode.NORMAL
    return RollMode.ADVANTAGE if advantage else RollMode.DISADVANTAGE


def _double_dice(expression: DiceExpression) -> DiceExpression:
    """Критическое попадание: костей урона вдвое больше, модификаторы те же."""
    terms: list[Term] = []
    for term in expression.terms:
        if isinstance(term, DiceTerm):
            keep = None if term.keep is None else Keep(term.keep.mode, term.keep.count * 2)
            terms.append(replace(term, count=term.count * 2, keep=keep))
        else:
            terms.append(term)
    return DiceExpression(terms=tuple(terms))


def _adjust_damage(
    target: Actor, rolled: int, damage_type: str | None
) -> tuple[int, tuple[DamageModifier, ...]]:
    """Урон с учётом иммунитета, сопротивления (вдвое меньше) и уязвимости (вдвое больше)."""
    if damage_type is None:
        return rolled, ()
    if damage_type in target.immunities:
        return 0, ("immunity",)
    amount = rolled
    modifiers: list[DamageModifier] = []
    if damage_type in target.resistances:
        amount //= 2
        modifiers.append("resistance")
    if damage_type in target.vulnerabilities:
        amount *= 2
        modifiers.append("vulnerability")
    return amount, tuple(modifiers)
