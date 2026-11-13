"""Глоссарий терминов (архитектура, раздел 18.2).

Один и тот же термин должен переводиться одинаково и в интерфейсе, и в ответах ИИ:
Armor Class — всегда «Класс доспеха (КД)», а не то «класс брони», то «защита».
Глоссарий состоит из двух частей: общие термины из файла и названия проверок,
состояний, типов урона и ресурсов из набора правил.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ai_dnd.i18n import LanguageCode
from ai_dnd.rules.ruleset import Ruleset

_TermId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class GlossaryTerm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: _TermId
    # Термин на каждом языке: {"en": "Armor Class", "ru": "Класс доспеха"}.
    text: Annotated[dict[LanguageCode, _Text], Field(min_length=1)]
    # Принятое сокращение, если есть: {"en": "AC", "ru": "КД"}.
    abbr: dict[LanguageCode, _Text] = Field(default_factory=dict)

    def in_language(self, language: str) -> str | None:
        """Термин на языке вместе с сокращением: «Класс доспеха (КД)»."""
        text = self.text.get(language)
        if text is None:
            return None
        abbr = self.abbr.get(language)
        return f"{text} ({abbr})" if abbr else text


class Glossary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terms: tuple[GlossaryTerm, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        seen: set[str] = set()
        for term in self.terms:
            if term.id in seen:
                raise ValueError(f"термин «{term.id}» записан дважды")
            seen.add(term.id)
        return self


def load_glossary(path: Path) -> Glossary:
    return Glossary.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def ruleset_terms(ruleset: Ruleset) -> list[GlossaryTerm]:
    """Термины из набора правил: проверки, спасброски, состояния, типы урона, ресурсы.

    Берутся только подписи, заданные сразу на нескольких языках.
    """
    groups: dict[str, Mapping[str, object]] = {
        "check": {key: item.label for key, item in ruleset.checks.items()},
        "save": {key: item.label for key, item in ruleset.saves.items()},
        "condition": {key: item.label for key, item in ruleset.conditions.items()},
        "damage": dict(ruleset.damage_types),
        "resource": {key: item.label for key, item in ruleset.resources.items()},
    }
    return [
        GlossaryTerm(id=f"{group}_{key}", text=label)
        for group, labels in groups.items()
        for key, label in labels.items()
        if isinstance(label, dict)
    ]


def translation_lines(
    terms: Iterable[GlossaryTerm], source: str = "en", target: str = "ru"
) -> list[str]:
    """Строки «термин — перевод» для промпта ИИ: «Armor Class (AC) — Класс доспеха (КД)».

    Термины без перевода на один из языков пропускаются; порядок сохраняется, чтобы
    неизменная часть промпта совпадала от хода к ходу.
    """
    lines: list[str] = []
    for term in terms:
        original = term.in_language(source)
        translated = term.in_language(target)
        if original is not None and translated is not None:
            lines.append(f"{original} — {translated}")
    return lines
