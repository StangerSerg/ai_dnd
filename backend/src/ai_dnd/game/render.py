"""События журнала текстом: для игроков (на языке игры) и для модели (на английском).

Игроки видят строки вроде «Мира — Скрытность: 16 против 15 — успех». Модель получает
те же факты коротким английским протоколом, чтобы описать раунд боя, ничего не выдумав.
"""

from ai_dnd.game.content import GameContent
from ai_dnd.game.events import (
    ActorsJoined,
    FactNoted,
    ItemGranted,
    JournalEvent,
    Narrated,
    NpcAttitudeChanged,
    PartyDefeated,
    PlayerActed,
    Revealed,
    SceneChanged,
)
from ai_dnd.game.session import SessionState
from ai_dnd.i18n import localize
from ai_dnd.rules.dice import ConstantTermResult, DiceTermResult, RollResult
from ai_dnd.rules.events import (
    ActorDied,
    ActorFled,
    AttackResolved,
    CheckResolved,
    CombatEnded,
    CombatStarted,
    ConditionApplied,
    ConditionRemoved,
    ContestResolved,
    DamageDealt,
    DiceRolled,
    Healed,
    ProtectionEnded,
    ProtectionStarted,
    ResourceChanged,
    Rested,
    RoundStarted,
    TimePassed,
    TurnStarted,
)
from ai_dnd.rules.ruleset import Outcome

_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "vs": "против",
        "vs_ac": "против КД",
        "hit": "попадание",
        "crit_hit": "критическое попадание",
        "miss": "промах",
        "damage": "{target} получает {amount} урона{kind} → хиты {hp}",
        "healed": "{target} восстанавливает {amount} хитов → {hp}",
        "died": "{actor} погибает",
        "fled": "{actor} сбегает с поля боя",
        "resource": "{actor}: {resource} {delta:+d} → {current}",
        "condition_on": "{actor}: {condition}",
        "condition_off": "{actor} больше не {condition}",
        "protect": "{protector} прикрывает {target}: удары по нему принимает защитник",
        "protect_off": "Прикрытие спало: {target} больше не защищён ({protector})",
        "intercept": " — удар принимает {protector}",
        "combat": "Бой! Порядок ходов: {order}",
        "round": "— Раунд {round} —",
        "turn": "Ход: {actor}",
        "combat_won": "Бой окончен: {side} на ногах",
        "combat_stopped": "Бой прекращён",
        "defeat": "☠ Отряд пал — поражение",
        "party": "отряд",
        "enemies": "противники",
        "time": "Проходит время: {rounds} раунд(ов)",
        "rest_short": "Короткий отдых",
        "rest_long": "Долгий отдых",
        "scene": "Сцена: {title}",
        "revealed": "Открыто: {text}",
        "attitude": "{npc}: отношение — {attitude}",
        "item": "{actor} получает: {item}",
        "joined": "Появляются: {names}",
        "fact": "Заметка ведущего: {text}",
        "contest": (
            "{actor} ({check}) {total} — {opponent} ({opponent_check}) {opponent_total}: {result}"
        ),
        "contest_win": "побеждает {winner}",
        "contest_tie": "ничья",
        "auto_fail": "провал без броска ({condition})",
        "friendly": "дружелюбное",
        "neutral": "нейтральное",
        "hostile": "враждебное",
    },
    "en": {
        "vs": "vs",
        "vs_ac": "vs AC",
        "hit": "hit",
        "crit_hit": "critical hit",
        "miss": "miss",
        "damage": "{target} takes {amount} damage{kind} → HP {hp}",
        "healed": "{target} regains {amount} HP → {hp}",
        "died": "{actor} dies",
        "fled": "{actor} flees the fight",
        "resource": "{actor}: {resource} {delta:+d} → {current}",
        "condition_on": "{actor} is {condition}",
        "condition_off": "{actor} is no longer {condition}",
        "protect": "{protector} protects {target}: the protector takes attacks aimed at them",
        "protect_off": "Protection ends: {target} is no longer covered by {protector}",
        "intercept": " — intercepted by {protector}",
        "combat": "Combat! Turn order: {order}",
        "round": "— Round {round} —",
        "turn": "Turn: {actor}",
        "combat_won": "The fight is over: the {side} stand",
        "combat_stopped": "The fight stops",
        "defeat": "☠ The party has fallen — defeat",
        "party": "party",
        "enemies": "enemies",
        "time": "Time passes: {rounds} round(s)",
        "rest_short": "Short rest",
        "rest_long": "Long rest",
        "scene": "Scene: {title}",
        "revealed": "Discovered: {text}",
        "attitude": "{npc}: attitude is now {attitude}",
        "item": "{actor} gets: {item}",
        "joined": "Appearing: {names}",
        "fact": "DM note: {text}",
        "contest": (
            "{actor} ({check}) {total} — {opponent} ({opponent_check}) {opponent_total}: {result}"
        ),
        "contest_win": "{winner} wins",
        "contest_tie": "a tie",
        "auto_fail": "fails without a roll ({condition})",
        "friendly": "friendly",
        "neutral": "neutral",
        "hostile": "hostile",
    },
}

_OUTCOMES: dict[str, dict[Outcome, str]] = {
    "ru": {
        Outcome.CRITICAL_FAILURE: "критический провал",
        Outcome.FAILURE: "провал",
        Outcome.PARTIAL_SUCCESS: "частичный успех",
        Outcome.SUCCESS: "успех",
        Outcome.CRITICAL_SUCCESS: "критический успех",
    },
    "en": {
        Outcome.CRITICAL_FAILURE: "critical failure",
        Outcome.FAILURE: "failure",
        Outcome.PARTIAL_SUCCESS: "partial success",
        Outcome.SUCCESS: "success",
        Outcome.CRITICAL_SUCCESS: "critical success",
    },
}


class EventRenderer:
    """Строки для игроков на языке игры (``language``) или для модели (``"en"``)."""

    def __init__(self, content: GameContent, language: str) -> None:
        self._content = content
        self._language = language if language in _TEXTS else "en"
        self._texts = _TEXTS[self._language]

    def line(self, event: JournalEvent, state: SessionState) -> str | None:
        """Строка о событии или ``None``, если показывать нечего (рассказ, конец хода)."""
        t = self._texts
        name = _Names(state)
        match event:
            case DiceRolled():
                return f"🎲 {name(event.actor_id)}: {format_roll(event.roll, self._language)}"
            case CheckResolved():
                label = self._check_label(event.check, event.kind)
                if event.auto_failed_by is not None:
                    condition = self._condition_label(event.auto_failed_by)
                    return f"{name(event.actor_id)} — {label}: " + t["auto_fail"].format(
                        condition=condition
                    )
                target = f" {t['vs']} {event.target}" if event.target is not None else ""
                outcome = _OUTCOMES[self._language][event.outcome]
                return f"{name(event.actor_id)} — {label}: {event.total}{target} — {outcome}"
            case ContestResolved():
                result = (
                    t["contest_win"].format(winner=name(event.winner_id))
                    if event.winner_id is not None
                    else t["contest_tie"]
                )
                return t["contest"].format(
                    actor=name(event.actor_id),
                    check=self._check_label(event.check, "check"),
                    total=event.total if event.total is not None else "—",
                    opponent=name(event.opponent_id),
                    opponent_check=self._check_label(event.opponent_check, "check"),
                    opponent_total=event.opponent_total
                    if event.opponent_total is not None
                    else "—",
                    result=result,
                )
            case AttackResolved():
                if event.outcome is Outcome.CRITICAL_SUCCESS:
                    verdict = t["crit_hit"]
                elif event.outcome.succeeded:
                    verdict = t["hit"]
                else:
                    verdict = t["miss"]
                intercept = (
                    t["intercept"].format(protector=name(event.redirected_to_id))
                    if event.redirected_to_id is not None
                    else ""
                )
                return (
                    f"⚔ {name(event.attacker_id)} → {name(event.target_id)} ({event.attack_name}): "
                    f"{event.total} {t['vs_ac']} {event.target_ac} — {verdict}{intercept}"
                )
            case DamageDealt():
                kind = f" ({self._damage_label(event.damage_type)})" if event.damage_type else ""
                return t["damage"].format(
                    target=name(event.target_id), amount=event.amount, kind=kind, hp=event.hp_after
                )
            case Healed():
                return t["healed"].format(
                    target=name(event.target_id), amount=event.amount, hp=event.hp_after
                )
            case ActorDied():
                return "☠ " + t["died"].format(actor=name(event.actor_id))
            case ActorFled():
                return t["fled"].format(actor=name(event.actor_id))
            case ResourceChanged():
                return t["resource"].format(
                    actor=name(event.actor_id),
                    resource=self._resource_label(event.resource),
                    delta=event.delta,
                    current=event.current,
                )
            case ConditionApplied():
                return t["condition_on"].format(
                    actor=name(event.actor_id), condition=self._condition_label(event.condition)
                )
            case ConditionRemoved():
                return t["condition_off"].format(
                    actor=name(event.actor_id), condition=self._condition_label(event.condition)
                )
            case ProtectionStarted():
                return "🛡 " + t["protect"].format(
                    protector=name(event.protector_id), target=name(event.target_id)
                )
            case ProtectionEnded():
                return t["protect_off"].format(
                    protector=name(event.protector_id), target=name(event.target_id)
                )
            case CombatStarted():
                return "⚔ " + t["combat"].format(order=", ".join(name(a) for a in event.order))
            case RoundStarted():
                return t["round"].format(round=event.round)
            case TurnStarted():
                return t["turn"].format(actor=name(event.actor_id))
            case CombatEnded():
                if event.winner is None:
                    return t["combat_stopped"]
                return t["combat_won"].format(side=t.get(event.winner, event.winner))
            case TimePassed():
                return t["time"].format(rounds=event.rounds)
            case Rested():
                return t["rest_short"] if event.rest == "short" else t["rest_long"]
            case SceneChanged():
                return "📍 " + t["scene"].format(title=self._scene_title(event.scene_id))
            case Revealed():
                return "🔎 " + t["revealed"].format(text=self._entity_text(event.entity_id))
            case NpcAttitudeChanged():
                return t["attitude"].format(
                    npc=self._npc_name(event.npc_id), attitude=t[event.attitude]
                )
            case ItemGranted():
                return "🎒 " + t["item"].format(actor=name(event.actor_id), item=event.name)
            case ActorsJoined():
                return t["joined"].format(names=", ".join(actor.name for actor in event.actors))
            case FactNoted():
                return t["fact"].format(text=event.text)
            case PartyDefeated():
                return t["defeat"]
            case PlayerActed() | Narrated():
                return None
            case _:
                return None

    # --- Подписи ---

    def _check_label(self, check_id: str, kind: str) -> str:
        if self._language == "en" and self._for_model:
            return check_id
        group = self._content.ruleset.checks if kind == "check" else self._content.ruleset.saves
        definition = group.get(check_id)
        return localize(definition.label, self._language) if definition else check_id

    def _condition_label(self, condition_id: str) -> str:
        definition = self._content.ruleset.conditions.get(condition_id)
        return localize(definition.label, self._language) if definition else condition_id

    def _damage_label(self, damage_type: str) -> str:
        label = self._content.ruleset.damage_types.get(damage_type)
        return localize(label, self._language) if label else damage_type

    def _resource_label(self, resource_id: str) -> str:
        definition = self._content.ruleset.resources.get(resource_id)
        return localize(definition.label, self._language) if definition else resource_id

    def _scene_title(self, scene_id: str) -> str:
        scene = next((s for s in self._content.scenario.scenes if s.id == scene_id), None)
        return scene.title if scene else scene_id

    def _npc_name(self, npc_id: str) -> str:
        npc = next((n for n in self._content.scenario.npcs if n.id == npc_id), None)
        return npc.name if npc else npc_id

    def _entity_text(self, entity_id: str) -> str:
        scenario = self._content.scenario
        for clue in scenario.clues:
            if clue.id == entity_id:
                return clue.text
        for handout in scenario.handouts:
            if handout.id == entity_id:
                return f"{handout.title} — {handout.text}"
        for item in scenario.items:
            if item.id == entity_id:
                return item.name
        for lore in scenario.lore:
            if lore.id == entity_id:
                return lore.title
        return entity_id

    @property
    def _for_model(self) -> bool:
        return False


class ModelLogRenderer(EventRenderer):
    """Протокол для модели: английские слова, id проверок вместо подписей."""

    def __init__(self, content: GameContent) -> None:
        super().__init__(content, "en")

    @property
    def _for_model(self) -> bool:
        return True

    def line(self, event: JournalEvent, state: SessionState) -> str | None:
        if isinstance(event, DiceRolled):
            # Броски модели не нужны: итог есть в следующем событии.
            return None
        if isinstance(event, PartyDefeated):
            # Финал поражения модель уже рассказала в Narrated; маркер — только игрокам.
            return None
        return super().line(event, state)


class _Names:
    def __init__(self, state: SessionState) -> None:
        self._actors = state.rules.actors

    def __call__(self, actor_id: str | None) -> str:
        if actor_id is None:
            return "—"
        actor = self._actors.get(actor_id)
        return actor.name if actor else actor_id


def format_roll(result: RollResult, language: str) -> str:
    """«1к20 [8] +7 = 15»; отброшенные кости — в скобках: «[(3) 15]»."""
    die = "к" if language == "ru" else "d"
    parts: list[str] = []
    for term in result.terms:
        sign = "-" if term.sign < 0 else "+"
        if isinstance(term, DiceTermResult):
            rolls = " ".join(
                str(item.value) if item.kept else f"({item.value})" for item in term.rolls
            )
            notation = term.notation.replace("d", die)
            text = f"{notation} [{rolls}]"
        elif isinstance(term, ConstantTermResult):
            text = str(term.value)
        else:
            continue
        parts.append(text if not parts and sign == "+" else f"{sign} {text}")
    return f"{' '.join(parts)} = {result.total}"
