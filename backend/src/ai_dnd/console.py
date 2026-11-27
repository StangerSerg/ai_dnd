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
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import TypeAdapter
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
from ai_dnd.importer.extract import extract_text
from ai_dnd.importer.pipeline import import_scenario
from ai_dnd.importer.report import entity_counts, write_result
from ai_dnd.rag.embeddings import Embedder
from ai_dnd.rag.search import LoreSearcher
from ai_dnd.rules.actions import (
    Action,
    AttackAction,
    EndTurnAction,
    FleeAction,
    HideAction,
    ProtectAction,
)
from ai_dnd.rules.errors import RulesError
from ai_dnd.rules.rng import Rng, SeededRng, SystemRng
from ai_dnd.rules.ruleset import Ruleset, RulesetRef
from ai_dnd.rules.visibility import PUBLIC
from ai_dnd.rulesets.service import load_ruleset, reseed_ruleset
from ai_dnd.scenarios.service import ScenarioError, load_bestiary, read_yaml
from ai_dnd.scenarios.store import (
    load_game_content_from_db,
    publish_scenario,
    resolve_current_version,
    scenario_visibility,
    version_refs,
)
from ai_dnd.sheets.compiled import load_template

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
        "hide": "Спрятаться",
        "protect": "Прикрыть союзника: {target} (хиты {hp})",
        "auto": "{name} может только закончить ход.",
        "cost": "Вызовов модели: {calls}, токенов на входе: {input} (из кэша {cache:.0%}), "
        "на выходе: {output}, стоимость: ${cost}",
        "help": "/sheet [имя] — карточка, /cost — расходы, /log [N] — журнал, /quit — выход",
        "llm_error": "Ведущий не смог ответить: {error}",
        "retry_narration": "Раунд не описан. Enter — повторить запрос, /quit — выйти",
        "retry_turn": "Ход не завершён. Enter — повторить, /quit — выйти",
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
        "hide": "Hide",
        "protect": "Protect ally: {target} (HP {hp})",
        "auto": "{name} can only end the turn.",
        "cost": "Model calls: {calls}, input tokens: {input} ({cache:.0%} cached), "
        "output: {output}, cost: ${cost}",
        "help": "/sheet [name] — sheet, /cost — spending, /log [N] — journal, /quit — exit",
        "llm_error": "The DM could not answer: {error}",
        "retry_narration": "The round is not narrated. Enter to retry, /quit to exit",
        "retry_turn": "The turn is unfinished. Enter to retry, /quit to exit",
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
                if self._master.has_pending_turn:
                    while await self._ask(self._t["retry_turn"]):
                        self._console.print(f"[yellow]{self._t['bad_choice']}[/yellow]")
                    await self._show(self._master.retry_turn())
                    continue
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
        await self._narrate_pending()
        await self._show_events(await master.run_creature_turns())
        await self._narrate_pending()
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
        await self._narrate_pending()

    async def _narrate_pending(self) -> None:
        while self._master.round_finished:
            if await self._show(self._master.narrate_combat()):
                return
            while await self._ask(self._t["retry_narration"]):
                self._console.print(f"[yellow]{self._t['bad_choice']}[/yellow]")

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
        if isinstance(action, HideAction):
            return self._t["hide"]
        if isinstance(action, ProtectAction):
            target = actors[action.target_id]
            hp = target.hp
            return self._t["protect"].format(
                target=target.name, hp=f"{hp.current}/{hp.max}" if hp else "—"
            )
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
            if not self._visible(event):
                continue
            if isinstance(event, PlayerActed):
                line: str | None = f"{state.rules.actors[event.actor_id].name}: {event.text}"
            elif isinstance(event, Narrated):
                line = f"{self._t['dm']}: {event.text}"
            else:
                line = self._renderer.line(event, state)
            if line:
                self._console.print(f"[dim]{escape(line)}[/dim]")

    # --- Вывод ---

    async def _show(self, updates: AsyncIterator[TurnUpdate]) -> bool:
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
            return False
        except Exception as error:
            if not (self._master.has_pending_turn or self._master.has_pending_combat_narration):
                raise
            self._break_line()
            self._console.print(
                f"[red]{escape(self._t['llm_error'].format(error=type(error).__name__))}[/red]"
            )
            return False
        self._break_line()
        return True

    async def _show_events(self, events: list[JournalEvent]) -> None:
        state = self._master.state
        for event in events:
            if not self._visible(event):
                continue
            line = self._renderer.line(event, state)
            if line is None:
                continue
            self._break_line()
            style = "cyan" if line.startswith(("🎲", "⚔")) else "green"
            self._console.print(f"[{style}]{escape(line)}[/{style}]")

    def _visible(self, event: JournalEvent) -> bool:
        return self._debug or (event.visibility == PUBLIC and not isinstance(event, FactNoted))

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
        await self._inner.append(events, state)
        self._log.extend(events)


async def _play(
    scenario: str,
    language: str,
    party: list[str],
    improvisation: Improvisation,
    fake_llm: bool,
    use_db: bool,
    seed: int | None,
    debug: bool,
    from_db: bool,
) -> None:
    settings = get_settings()
    engine = create_engine(settings) if use_db else None
    sessions = create_session_factory(engine) if engine is not None else None
    version_id: uuid.UUID | None = None
    ruleset_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    if from_db:
        # from_db требует базу (проверено в команде play); sessions здесь не None.
        assert sessions is not None
        version_id = await resolve_current_version(sessions, scenario, language)
        content = await load_game_content_from_db(sessions, version_id, settings.content_dir)
        ruleset_id, template_id = await version_refs(sessions, version_id)
    else:
        content = load_game_content(settings.content_dir, scenario, language)
    seats, actors = load_party(settings.content_dir, content, party)
    setup = SessionSetup(
        scenario=scenario, language=language, improvisation=improvisation, seats=tuple(seats)
    )
    state = new_session_state(content, actors)

    memory = MemoryRecorder()
    recorder: CallRecorder = memory
    journal: Journal = MemoryJournal()
    if sessions is not None:
        recorder = CompositeRecorder(memory, DbRecorder(sessions))
        journal = await DbJournal.create(
            sessions,
            setup,
            state,
            scenario_version_id=version_id,
            ruleset_id=ruleset_id,
            sheet_template_id=template_id,
        )

    log: list[JournalEvent] = []
    gateway = build_gateway(settings, recorder, fake=fake_llm)
    rng: Rng = SeededRng(seed) if seed is not None else SystemRng()
    searcher: LoreSearcher | None = None
    if from_db and sessions is not None and version_id is not None:
        searcher = LoreSearcher(
            sessions,
            version_id,
            Embedder(settings.embedding_model, settings.embedding_dim),
            language,
        )
    master = GameMaster(
        content, setup, state, gateway, _LoggingJournal(journal, log), rng, searcher
    )
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
    from_db: Annotated[
        bool, typer.Option("--from-db", help="Играть по опубликованной версии сценария из базы")
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
    if from_db and no_db:
        raise typer.BadParameter("--from-db требует базу: уберите --no-db")
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
            from_db,
        )
    )


async def _seed(
    scenario: str, language: str, visibility: str | None, index: bool, wait: bool
) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    console = Console(highlight=False)
    try:
        try:
            previous_id = await resolve_current_version(sessions, scenario, language)
        except FileNotFoundError:
            previous_id = None
        version_id = await publish_scenario(
            sessions,
            settings.content_dir,
            scenario,
            language,
            visibility=visibility,
            new_visibility="public",
        )
        saved_visibility = await scenario_visibility(sessions, version_id)
        console.print(
            f"Опубликована версия {version_id} сценария {scenario}.{language} ({saved_visibility})"
        )
        if not index:
            console.print("Индексация пропущена (--no-index): воркер не вызывается.")
            return
        # Публикация связана с постановкой задачи воркера: индекс считает воркер,
        # предыдущая версия передаётся для переиспользования фрагментов.
        from ai_dnd.tasks import index_scenario_version

        task = await index_scenario_version.kiq(
            str(version_id), str(previous_id) if previous_id is not None else None
        )
        console.print(f"Индексация поставлена в очередь воркера: {task.task_id}")
        if wait:
            result = await task.wait_result(timeout=600)
            result.raise_for_error()
            console.print(f"Проиндексировано воркером: {result.return_value}")
    finally:
        await engine.dispose()


@app.command()
def seed(
    scenario: Annotated[
        str, typer.Option(help="Сценарий из content/scenarios")
    ] = "shadow-over-millford",
    lang: Annotated[str, typer.Option(help="Язык: ru или en")] = "ru",
    visibility: Annotated[
        str | None, typer.Option(help="Видимость: private, unlisted или public (новый: public)")
    ] = None,
    no_index: Annotated[
        bool, typer.Option("--no-index", help="Не индексировать: задачу воркеру не ставить")
    ] = False,
    wait: Annotated[
        bool, typer.Option("--wait", help="Дождаться результата индексации воркером")
    ] = False,
) -> None:
    """Опубликовать сценарий как новую версию и поставить индексацию в очередь воркера."""
    if lang not in _UI:
        raise typer.BadParameter("lang: ru или en")
    if visibility is not None and visibility not in ("private", "unlisted", "public"):
        raise typer.BadParameter("visibility: private, unlisted или public")
    asyncio.run(_seed(scenario, lang, visibility, not no_index, wait))


@app.command("reseed-ruleset")
def reseed_ruleset_command(
    ref: Annotated[str, typer.Argument(help="Точная ссылка на существующую версию: id@version")],
) -> None:
    """Осознанно заменить данные опубликованной версии ruleset из content/rulesets."""
    console = Console(highlight=False)
    try:
        TypeAdapter(RulesetRef).validate_python(ref)
        settings = get_settings()
        ruleset = load_ruleset(settings.content_dir / "rulesets" / f"{ref.split('@', 1)[0]}.json")
        if ruleset.ref != ref:
            raise ValueError(f"ruleset: файл содержит {ruleset.ref}, ожидался {ref}")
        row_id = asyncio.run(_reseed_ruleset(ruleset))
    except (ValueError, OSError, RulesError) as error:
        console.print(f"[red]{escape(str(error))}[/red]")
        raise typer.Exit(1) from error
    console.print(f"Обновлены данные ruleset {escape(ref)} ({row_id})")


async def _reseed_ruleset(ruleset: Ruleset) -> uuid.UUID:
    engine = create_engine(get_settings())
    try:
        return await reseed_ruleset(create_session_factory(engine), ruleset)
    finally:
        await engine.dispose()


@app.command()
def reindex(
    version_id: Annotated[str, typer.Argument(help="UUID опубликованной версии сценария")],
    previous: Annotated[
        str | None, typer.Option(help="UUID предыдущей версии для переиспользования фрагментов")
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help="Дождаться результата воркера")] = False,
) -> None:
    """Поставить индексацию версии в очередь воркера (нужны Redis и запущенный воркер)."""
    asyncio.run(_reindex(version_id, previous, wait))


async def _reindex(version_id: str, previous: str | None, wait: bool) -> None:
    from ai_dnd.tasks import index_scenario_version

    console = Console(highlight=False)
    task = await index_scenario_version.kiq(version_id, previous)
    console.print(f"Задача индексации поставлена в очередь: {task.task_id}")
    if wait:
        result = await task.wait_result(timeout=600)
        try:
            result.raise_for_error()
        except Exception as error:
            console.print(f"[red]Ошибка воркера:[/red] {error}")
            raise typer.Exit(1) from error
        console.print(f"Результат воркера: {result.return_value}")


async def _import(
    file: Path,
    language: str,
    out: Path,
    ruleset_ref: str,
    template_ref: str,
    fake_llm: bool,
    force: bool,
) -> None:
    console = Console(highlight=False)
    settings = get_settings()
    try:
        # Правленый автором YAML не перезаписываем молча: только с явным --force.
        # Проверка до чтения файла и вызова модели, чтобы не тратить их впустую.
        yaml_target = out / f"{file.stem}.{language}.yaml"
        if yaml_target.exists() and not force:
            msg = (
                f"output_exists: {yaml_target} уже существует; измените --out или добавьте --force"
            )
            raise ValueError(msg)
        # Ссылки вида «dnd5e-srd@1» → файл dnd5e-srd.json; версия сверяется с содержимым.
        ruleset = load_ruleset(
            settings.content_dir / "rulesets" / f"{ruleset_ref.split('@', 1)[0]}.json"
        )
        if ruleset.ref != ruleset_ref:
            raise ValueError(f"ruleset: файл содержит {ruleset.ref}, ожидался {ruleset_ref}")
        template = load_template(
            settings.content_dir / "sheets" / f"{template_ref.split('@', 1)[0]}.json"
        )
        if template.ref != template_ref:
            raise ValueError(f"template: файл содержит {template.ref}, ожидался {template_ref}")
        bestiaries = tuple(
            load_bestiary(path, ruleset)
            for path in sorted((settings.content_dir / "bestiary").glob("*.yaml"))
            if read_yaml(path).get("ruleset") == ruleset.ref
        )
        extracted = extract_text(file)
        # Импорт не пишет в базу: стоимость видна только в отчёте и выводе команды.
        gateway = build_gateway(settings, MemoryRecorder(), fake=fake_llm)
        result = await import_scenario(
            gateway,
            text=extracted.text,
            language=language,
            ruleset=ruleset,
            bestiaries=bestiaries,
            sheet_template=template_ref,
        )
        yaml_path, report_path = write_result(
            out, file.stem, result, source_name=file.name, language=language
        )
    except (ValueError, LLMError, FileNotFoundError, ScenarioError) as error:
        console.print(f"[red]{escape(str(error))}[/red]")
        raise typer.Exit(1) from error
    counts = ", ".join(
        f"{name}: {count}" for name, count in entity_counts(result.draft.scenario).items()
    )
    console.print(f"YAML: {escape(str(yaml_path))}")
    console.print(f"Отчёт: {escape(str(report_path))}")
    console.print(f"Сущности — {counts}")
    if result.issues:
        # Не ошибка запуска: автор правит YAML по отчёту и публикует командой seed.
        console.print(
            f"[yellow]Проблемы валидации: {len(result.issues)} — "
            "исправьте YAML перед публикацией (список в отчёте)[/yellow]"
        )
    else:
        console.print("Проблемы валидации: 0")
    console.print(f"Заметки модели: {len(result.draft.notes)}")
    console.print(f"Вызовы модели: {len(result.responses)}, стоимость: ${result.cost}")


@app.command(name="import")
def import_module(
    file: Annotated[Path, typer.Argument(help="Файл автора: .md/.markdown/.txt/.docx/.pdf")],
    lang: Annotated[str, typer.Option(help="Язык сценария: ru или en")] = "ru",
    out: Annotated[Path, typer.Option(help="Куда положить YAML-черновик и отчёт")] = Path("."),
    ruleset: Annotated[str, typer.Option(help="Ссылка на набор правил")] = "dnd5e-srd@1",
    template: Annotated[str, typer.Option(help="Ссылка на шаблон карточки")] = "srd5-basic@1",
    fake_llm: Annotated[
        bool, typer.Option("--fake-llm", help="Модель-заглушка: без денег и сети")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Перезаписать существующий YAML-черновик и отчёт")
    ] = False,
) -> None:
    """Импортировать сценарий из файла автора: YAML-черновик и отчёт (без базы)."""
    if lang not in _UI:
        raise typer.BadParameter("lang: ru или en")
    asyncio.run(_import(file, lang, out, ruleset, template, fake_llm, force))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
