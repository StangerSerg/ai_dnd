"""Консольный клиент: игра с ведущим-ИИ в терминале (подэтап 1.7).

    uv run ai-dnd-play --lang ru --party fighter,rogue,cleric
    uv run ai-dnd-play --fake-llm --no-db      # без денег, сети и базы

Несколько игроков за одной клавиатурой: клиент по очереди спрашивает каждого, что
делает его персонаж, и отдаёт ведущему все действия разом. Пустой ответ — персонаж
ничего не делает. В бою — выбор из списка допустимых действий.

Команды: /sheet [имя], /cost, /log [N], /help, /quit.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ai_dnd.ai.gateway import build_gateway
from ai_dnd.ai.provider import LLMError
from ai_dnd.ai.recorder import CallRecorder, CompositeRecorder, DbRecorder, MemoryRecorder
from ai_dnd.config import get_settings
from ai_dnd.db.engine import create_engine, create_session_factory
from ai_dnd.game.content import GameContent, load_game_content, load_party, new_session_state
from ai_dnd.game.events import FactNoted, JournalEvent, Narrated, PlayerActed
from ai_dnd.game.journal import DbJournal, Journal, MemoryJournal
from ai_dnd.game.master import (
    EventsHappened,
    GameMaster,
    ModelCalled,
    NarrationChunk,
    PlayerInput,
    ToolUsed,
    TurnUpdate,
)
from ai_dnd.game.render import EventRenderer
from ai_dnd.game.session import Improvisation, SessionSetup, SessionState
from ai_dnd.i18n import localize
from ai_dnd.rules.actions import Action, AttackAction, EndTurnAction, FleeAction
from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.rng import Rng, SeededRng, SystemRng
from ai_dnd.rules.visibility import PUBLIC

app = typer.Typer(add_completion=False, help="Игра с ведущим-ИИ в терминале.")

_UI: dict[str, dict[str, str]] = {
    "ru": {
        "title": "AI DnD — консоль",
        "prompt": "{name} (что делаешь?)",
        "nobody": "Никто ничего не сделал. Опишите действия хотя бы одного персонажа.",
        "dm": "Ведущий",
        "your_turn": "Ход: {name}. Выберите действие:",
        "choice": "Номер",
        "bad_choice": "Нет такого варианта.",
        "attack": "Атаковать: {attack} → {target} (КД {ac}, хиты {hp})",
        "flee": "Сбежать из боя",
        "end_turn": "Закончить ход",
        "auto": "{name} может только закончить ход.",
        "cost": "Вызовов модели: {calls}, токенов на входе: {input} (из кэша {cache:.0%}), "
        "на выходе: {output}, стоимость: ${cost}",
        "help": "/sheet [имя] — карточка, /cost — расходы, /log [N] — журнал, /quit — выход",
        "llm_error": "Ведущий не смог ответить: {error}",
        "bye": "Игра сохранена в журнале. До встречи!",
        "unknown_command": "Неизвестная команда. /help — список команд.",
        "no_actor": "Нет такого персонажа.",
        "session": "Сессия {id}",
    },
    "en": {
        "title": "AI DnD — console",
        "prompt": "{name} (what do you do?)",
        "nobody": "Nobody did anything. Describe at least one character's action.",
        "dm": "Dungeon Master",
        "your_turn": "{name}'s turn. Choose an action:",
        "choice": "Number",
        "bad_choice": "No such option.",
        "attack": "Attack: {attack} → {target} (AC {ac}, HP {hp})",
        "flee": "Flee the fight",
        "end_turn": "End turn",
        "auto": "{name} can only end the turn.",
        "cost": "Model calls: {calls}, input tokens: {input} ({cache:.0%} cached), "
        "output: {output}, cost: ${cost}",
        "help": "/sheet [name] — sheet, /cost — spending, /log [N] — journal, /quit — exit",
        "llm_error": "The DM could not answer: {error}",
        "bye": "The game is saved in the journal. See you!",
        "unknown_command": "Unknown command. /help lists commands.",
        "no_actor": "No such character.",
        "session": "Session {id}",
    },
}


class _QuitError(Exception):
    pass


class ConsoleGame:
    def __init__(
        self,
        master: GameMaster,
        content: GameContent,
        recorder: MemoryRecorder,
        journal_events: list[JournalEvent],
        *,
        session_id: uuid.UUID | None,
        debug: bool,
    ) -> None:
        self._master = master
        self._content = content
        self._recorder = recorder
        self._log = journal_events
        self._session_id = session_id
        self._debug = debug
        self._language = master.setup.language
        self._t = _UI.get(self._language, _UI["en"])
        self._renderer = EventRenderer(content, self._language)
        self._console = Console(highlight=False)
        self._mid_line = False

    async def run(self) -> None:
        self._console.rule(f"[bold]{escape(self._content.scenario.scenario.title)}")
        if self._session_id is not None:
            self._console.print(f"[dim]{self._t['session'].format(id=self._session_id)}[/dim]")
        self._console.print(f"[dim]{escape(self._t['help'])}[/dim]\n")
        try:
            await self._show(self._master.open_session())
            while True:
                if self._master.in_combat:
                    await self._combat()
                    continue
                inputs = await self._collect_actions()
                await self._show(self._master.play_turn(inputs))
        except _QuitError:
            self._console.print(f"\n{self._t['bye']}")
        finally:
            self._cost()

    # --- Свободная игра ---

    async def _collect_actions(self) -> list[PlayerInput]:
        while True:
            inputs: list[PlayerInput] = []
            for seat in self._master.setup.seats:
                actor = self._master.state.rules.actors[seat.actor_id]
                if actor.dead:
                    continue
                text = await self._ask(self._t["prompt"].format(name=seat.name))
                if text:
                    inputs.append(PlayerInput(actor_id=seat.actor_id, text=text))
            if inputs:
                return inputs
            self._console.print(f"[yellow]{self._t['nobody']}[/yellow]")

    # --- Бой ---

    async def _combat(self) -> None:
        master = self._master
        await self._show_events(await master.run_creature_turns())
        if master.round_finished:
            await self._show(master.narrate_combat())
        if not master.in_combat:
            return
        current = master.current_combatant
        if current is None or not master.is_party(current):
            return

        name = master.state.rules.actors[current].name
        options = master.combat_options(current)
        if len(options) == 1:
            self._console.print(f"[dim]{self._t['auto'].format(name=escape(name))}[/dim]")
            action = options[0]
        else:
            action = await self._choose(name, options)
        try:
            events = await master.act(action)
        except RulesError as error:
            self._console.print(f"[red]{escape(str(error))}[/red]")
            return
        await self._show_events(events)
        if master.round_finished:
            await self._show(master.narrate_combat())

    async def _choose(self, name: str, options: list[Action]) -> Action:
        self._console.print(f"\n[bold]{escape(self._t['your_turn'].format(name=name))}[/bold]")
        for number, option in enumerate(options, start=1):
            self._console.print(f"  {number}. {escape(self._describe(option))}")
        while True:
            answer = await self._ask(self._t["choice"])
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self._console.print(f"[yellow]{self._t['bad_choice']}[/yellow]")

    def _describe(self, action: Action) -> str:
        actors = self._master.state.rules.actors
        if isinstance(action, AttackAction):
            target = actors[action.target_id]
            hp = target.hp
            return self._t["attack"].format(
                attack=actors[action.attacker_id].attacks[action.attack].name,
                target=target.name,
                ac=target.ac,
                hp=f"{hp.current}/{hp.max}" if hp else "—",
            )
        if isinstance(action, FleeAction):
            return self._t["flee"]
        if isinstance(action, EndTurnAction):
            return self._t["end_turn"]
        return action.type

    # --- Ввод и команды ---

    async def _ask(self, prompt: str) -> str:
        """Строка от игрока. Команды выполняются тут же, и вопрос задаётся снова."""
        while True:
            self._break_line()
            try:
                text = await asyncio.to_thread(
                    self._console.input, f"[bold cyan]{escape(prompt)}[/bold cyan] > "
                )
            except (EOFError, KeyboardInterrupt):
                raise _QuitError() from None
            text = text.strip()
            if not text.startswith("/"):
                return text
            self._command(text)

    def _command(self, text: str) -> None:
        name, _, argument = text[1:].partition(" ")
        argument = argument.strip()
        if name in ("quit", "exit", "q"):
            raise _QuitError()
        if name == "help":
            self._console.print(escape(self._t["help"]))
        elif name == "sheet":
            self._sheet(argument)
        elif name == "cost":
            self._cost()
        elif name == "log":
            self._show_log(int(argument) if argument.isdigit() else 15)
        else:
            self._console.print(f"[yellow]{self._t['unknown_command']}[/yellow]")

    def _sheet(self, query: str) -> None:
        state = self._master.state
        seats = self._master.setup.seats
        seat = next(
            (s for s in seats if not query or query.lower() in (s.name.lower(), s.actor_id)),
            None,
        )
        if seat is None:
            self._console.print(f"[yellow]{self._t['no_actor']}[/yellow]")
            return
        actor = state.rules.actors[seat.actor_id]
        ruleset = self._content.ruleset
        table = Table(title=f"{actor.name} — {seat.summary}", show_header=False, box=None)
        hp = actor.hp
        table.add_row("HP", f"{hp.current}/{hp.max}" if hp else "—")
        table.add_row("AC", str(actor.ac))
        for resource_id, resource in actor.resources.items():
            # Хиты уже показаны, а пустые ресурсы (ячейки заклинаний у плута) — шум.
            if resource_id == "hp" or resource.max == 0:
                continue
            definition = ruleset.resources.get(resource_id)
            label = localize(definition.label, self._language) if definition else resource_id
            table.add_row(label, f"{resource.current}/{resource.max}")
        if actor.conditions:
            table.add_row("⚠", ", ".join(item.id for item in actor.conditions))
        checks = ", ".join(
            f"{localize(ruleset.checks[check].label, self._language)} {bonus:+d}"
            for check, bonus in actor.checks.items()
            if check in ruleset.checks and ruleset.checks[check].ability is not None
        )
        table.add_row("🎯", checks)
        attacks = ", ".join(
            f"{attack.name} {attack.bonus:+d} ({attack.damage or '—'})"
            for attack in actor.attacks.values()
        )
        table.add_row("⚔", attacks)
        items = state.inventory.get(actor.id, ())
        if items:
            table.add_row("🎒", ", ".join(item.name for item in items))
        self._console.print(table)

    def _cost(self) -> None:
        calls = self._recorder.calls
        total_input = sum(call.usage.input_tokens for call in calls)
        cached = sum(call.usage.cached_input_tokens for call in calls)
        output = sum(call.usage.output_tokens for call in calls)
        self._console.print(
            self._t["cost"].format(
                calls=len(calls),
                input=total_input,
                cache=cached / total_input if total_input else 0.0,
                output=output,
                cost=self._recorder.total_cost.quantize(Decimal("0.000001")),
            )
        )

    def _show_log(self, count: int) -> None:
        state = self._master.state
        for event in self._log[-count:]:
            if isinstance(event, PlayerActed):
                line: str | None = f"{state.rules.actors[event.actor_id].name}: {event.text}"
            elif isinstance(event, Narrated):
                line = f"{self._t['dm']}: {event.text}"
            else:
                line = self._renderer.line(event, state)
            if line:
                self._console.print(f"[dim]{escape(line)}[/dim]")

    # --- Вывод ---

    async def _show(self, updates: AsyncIterator[TurnUpdate]) -> None:
        started = False
        try:
            async for update in updates:
                if isinstance(update, NarrationChunk):
                    if not started:
                        self._break_line()
                        self._console.print(
                            f"[bold magenta]{self._t['dm']}:[/bold magenta] ", end=""
                        )
                        started = True
                    self._console.print(escape(update.text), end="", soft_wrap=True)
                    self._mid_line = not update.text.endswith("\n")
                elif isinstance(update, EventsHappened):
                    await self._show_events(list(update.events))
                elif isinstance(update, ToolUsed) and self._debug:
                    self._break_line()
                    self._console.print(
                        f"[dim]⚙ {update.name}({update.arguments}) → {update.result}[/dim]"
                    )
                elif isinstance(update, ModelCalled) and self._debug:
                    usage = update.response.usage
                    self._break_line()
                    self._console.print(
                        f"[dim]· {update.response.model}: {usage.input_tokens} in "
                        f"({usage.cache_share:.0%} cached), {usage.output_tokens} out, "
                        f"${update.response.cost}[/dim]"
                    )
        except LLMError as error:
            self._break_line()
            self._console.print(f"[red]{escape(self._t['llm_error'].format(error=error))}[/red]")
        self._break_line()

    async def _show_events(self, events: list[JournalEvent]) -> None:
        state = self._master.state
        for event in events:
            if event.visibility != PUBLIC and not self._debug:
                continue
            if isinstance(event, FactNoted) and not self._debug:
                continue
            line = self._renderer.line(event, state)
            if line is None:
                continue
            self._break_line()
            style = "cyan" if line.startswith(("🎲", "⚔")) else "green"
            self._console.print(f"[{style}]{escape(line)}[/{style}]")

    def _break_line(self) -> None:
        if self._mid_line:
            self._console.print()
            self._mid_line = False


class _LoggingJournal:
    """Журнал, который ещё и запоминает события для команды /log."""

    def __init__(self, inner: Journal, log: list[JournalEvent]) -> None:
        self._inner = inner
        self._log = log

    @property
    def session_id(self) -> uuid.UUID | None:
        return self._inner.session_id

    async def append(self, events: Sequence[JournalEvent], state: SessionState) -> None:
        self._log.extend(events)
        await self._inner.append(events, state)


async def _play(
    scenario: str,
    language: str,
    party: list[str],
    improvisation: Improvisation,
    fake_llm: bool,
    use_db: bool,
    seed: int | None,
    debug: bool,
) -> None:
    settings = get_settings()
    content = load_game_content(settings.content_dir, scenario, language)
    seats, actors = load_party(settings.content_dir, content, party)
    setup = SessionSetup(
        scenario=scenario, language=language, improvisation=improvisation, seats=tuple(seats)
    )
    state = new_session_state(content, actors)

    memory = MemoryRecorder()
    recorder: CallRecorder = memory
    journal: Journal = MemoryJournal()
    engine = None
    if use_db:
        engine = create_engine(settings)
        sessions = create_session_factory(engine)
        recorder = CompositeRecorder(memory, DbRecorder(sessions))
        journal = await DbJournal.create(sessions, setup, state)

    log: list[JournalEvent] = []
    gateway = build_gateway(settings, recorder, fake=fake_llm)
    rng: Rng = SeededRng(seed) if seed is not None else SystemRng()
    master = GameMaster(content, setup, state, gateway, _LoggingJournal(journal, log), rng)
    game = ConsoleGame(master, content, memory, log, session_id=journal.session_id, debug=debug)
    try:
        await game.run()
    finally:
        if engine is not None:
            await engine.dispose()


@app.command()
def play(
    scenario: Annotated[
        str, typer.Option(help="Сценарий из content/scenarios")
    ] = "shadow-over-millford",
    lang: Annotated[str, typer.Option(help="Язык игры: ru или en")] = "ru",
    party: Annotated[
        str, typer.Option(help="Персонажи из content/characters через запятую")
    ] = "fighter,rogue,cleric",
    improvisation: Annotated[str, typer.Option(help="strict, moderate или free")] = "moderate",
    fake_llm: Annotated[
        bool, typer.Option("--fake-llm", help="Модель-заглушка: без денег и сети")
    ] = False,
    no_db: Annotated[
        bool, typer.Option("--no-db", help="Не писать журнал и расходы в базу")
    ] = False,
    seed: Annotated[int | None, typer.Option(help="Seed бросков для воспроизводимой игры")] = None,
    debug: Annotated[
        bool, typer.Option("--debug", help="Показывать вызовы инструментов и расходы")
    ] = False,
) -> None:
    """Сыграть сценарий с ведущим-ИИ."""
    if improvisation not in ("strict", "moderate", "free"):
        raise typer.BadParameter("improvisation: strict, moderate или free")
    if lang not in _UI:
        raise typer.BadParameter("lang: ru или en")
    members = [name.strip() for name in party.split(",") if name.strip()]
    asyncio.run(
        _play(
            scenario,
            lang,
            members,
            cast(Improvisation, improvisation),
            fake_llm,
            not no_db,
            seed,
            debug,
        )
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
