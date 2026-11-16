"""Встроенный контент, собранный для игры: правила, шаблон карточки, монстры, сценарий,
глоссарий и готовые персонажи (папка ``content/``)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_dnd.characters.service import build_sheet, load_character
from ai_dnd.game.actors import actor_from_sheet
from ai_dnd.game.session import PlayerSeat, SessionState
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.rules.state import Actor, GameState
from ai_dnd.rulesets.glossary import Glossary, load_glossary
from ai_dnd.rulesets.service import load_ruleset
from ai_dnd.scenarios.schemas import Bestiary, Scenario
from ai_dnd.scenarios.service import load_bestiary, load_scenario, read_yaml
from ai_dnd.sheets.compiled import CompiledTemplate, load_template


@dataclass(frozen=True, slots=True)
class GameContent:
    ruleset: Ruleset
    template: CompiledTemplate
    bestiaries: tuple[Bestiary, ...]
    scenario: Scenario
    glossary: Glossary

    @property
    def language(self) -> str:
        return self.scenario.scenario.language


def load_game_content(content_dir: Path, scenario: str, language: str) -> GameContent:
    """Сценарий ``content/scenarios/<scenario>.<language>.yaml`` и всё, что ему нужно."""
    path = content_dir / "scenarios" / f"{scenario}.{language}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Нет сценария {path}")
    # Набор правил и шаблон берутся по ссылке из сценария: «dnd5e-srd@1» → dnd5e-srd.json.
    # Ссылки сценария проверяются ниже, когда известны правила и монстры.
    preview = Scenario.model_validate(read_yaml(path))
    ruleset = load_ruleset(content_dir / "rulesets" / f"{_ref_id(preview.scenario.ruleset)}.json")
    template = load_template(
        content_dir / "sheets" / f"{_ref_id(preview.scenario.sheet_template)}.json"
    )
    bestiaries = tuple(
        load_bestiary(item, ruleset) for item in sorted((content_dir / "bestiary").glob("*.yaml"))
    )
    return GameContent(
        ruleset=ruleset,
        template=template,
        bestiaries=bestiaries,
        scenario=load_scenario(path, bestiaries, ruleset),
        glossary=load_glossary(content_dir / "glossary.yaml"),
    )


def load_party(
    content_dir: Path, content: GameContent, characters: Sequence[str]
) -> tuple[list[PlayerSeat], list[Actor]]:
    """Готовые персонажи ``content/characters/<язык>/<имя>.json``.

    Id участника — имя файла (``rogue``), игроки — ``p1``, ``p2`` и так далее.
    """
    seats: list[PlayerSeat] = []
    actors: list[Actor] = []
    for number, name in enumerate(characters, start=1):
        character = load_character(content_dir / "characters" / content.language / f"{name}.json")
        sheet = build_sheet(character, content.template)
        display = str(sheet.values.get("name") or name)
        actors.append(actor_from_sheet(name, display, sheet))
        seats.append(
            PlayerSeat(
                player_id=f"p{number}",
                actor_id=name,
                name=display,
                summary=_summary(sheet.values),
            )
        )
    return seats, actors


def new_session_state(content: GameContent, party: Sequence[Actor]) -> SessionState:
    """Начало игры: отряд в стартовой сцене, открыты только публичные материалы."""
    scenario = content.scenario
    start = scenario.scenario.start_scene
    public_handouts = tuple(
        handout.id for handout in scenario.handouts if handout.visibility == "public"
    )
    return SessionState(
        rules=GameState(actors={actor.id: actor for actor in party}),
        scene_id=start,
        visited=(start,),
        revealed=public_handouts,
    )


def _ref_id(ref: str) -> str:
    return ref.split("@", 1)[0]


def _summary(values: dict[str, object]) -> str:
    """«Полурослик (легконогий), Плут 3» — из полей карточки, если они есть."""
    race = values.get("race")
    char_class = values.get("char_class")
    level = values.get("level")
    parts = [str(race)] if race else []
    if char_class:
        parts.append(f"{char_class} {level}" if level else str(char_class))
    return ", ".join(parts)
