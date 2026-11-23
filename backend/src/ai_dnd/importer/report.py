"""YAML-черновик и Markdown-отчёт импорта для автора (подэтап 2.5).

Черновик — обычный сценарий схемы 8.1: автор правит YAML и публикует его командой
``seed``. Отчёт собирает счётчики сущностей, проблемы валидации, заметки модели и
стоимость вызовов, чтобы автор знал, что смотреть в первую очередь.
"""

from pathlib import Path

import yaml

from ai_dnd.importer.pipeline import ImportResult
from ai_dnd.rules.ruleset import Effect
from ai_dnd.scenarios.schemas import Item, ItemBuff, ItemHeal, ItemSpell, Scenario

_KIND_LABELS = {"assumption": "Допущение", "question": "Вопрос", "missing": "Не хватает данных"}


def entity_counts(scenario: Scenario) -> dict[str, int]:
    """Сколько сущностей каждого типа в черновике (statblocks — словарь, не кортеж)."""
    return {
        "scenes": len(scenario.scenes),
        "npcs": len(scenario.npcs),
        "items": len(scenario.items),
        "clues": len(scenario.clues),
        "handouts": len(scenario.handouts),
        "encounters": len(scenario.encounters),
        "lore": len(scenario.lore),
        "statblocks": len(scenario.statblocks),
    }


def _effect_label(effect: Effect) -> str:
    """Эффект одной строкой: «checks(athletics) +2», «saves advantage», «attacks +1»."""
    target = str(effect.applies_to)
    if effect.only:
        target += "(" + ", ".join(effect.only) + ")"
    parts = []
    if effect.mode is not None:
        parts.append(str(effect.mode))
    if effect.bonus:
        parts.append(f"{effect.bonus:+d}")
    if effect.auto_fail:
        parts.append("автопровал")
    return " ".join([target, *parts])


def item_recognized_props(item: Item) -> list[str]:
    """Распознанные механические свойства предмета — как их видит автор в отчёте."""
    props = []
    if item.kind is not None:
        props.append(f"тип {item.kind}")
    if item.slot is not None:
        props.append(f"слот {item.slot}")
    if item.quantity != 1:
        props.append(f"количество {item.quantity}")
    if item.enhancement:
        props.append(f"заточка +{item.enhancement}")
    if item.ac_bonus:
        props.append(f"AC {item.ac_bonus:+d}")
    if item.damage_bonus:
        props.append(f"урон {item.damage_bonus:+d}")
    if item.damage_type is not None:
        props.append(f"тип урона {item.damage_type}")
    props.extend(f"модификатор {_effect_label(effect)}" for effect in item.modifiers)
    use = item.consumable_effect
    if isinstance(use, ItemHeal):
        props.append(f"лечение {use.amount}")
    elif isinstance(use, ItemBuff):
        effects = "; ".join(_effect_label(effect) for effect in use.effects)
        duration = f"на {use.rounds} раундов" if use.rounds is not None else "до снятия"
        props.append(f"баф ({duration}): {effects}")
    elif isinstance(use, ItemSpell):
        props.append(f"заклинание «{use.spell}»")
    return props


def _item_property_lines(result: ImportResult) -> list[str]:
    """Секция «распознано / под вопросом»: свойства из черновика + заметки и issues по предмету."""
    scenario = result.draft.scenario
    if not scenario.items:
        return ["предметов нет"]
    lines: list[str] = []
    narrative = 0
    for item in scenario.items:
        prefix = f"items.{item.id}"
        notes = [
            note
            for note in result.draft.notes
            if note.path == prefix or note.path.startswith(f"{prefix}.")
        ]
        issues = [
            issue
            for issue in result.issues
            if issue.path == prefix or issue.path.startswith(f"{prefix}.")
        ]
        props = item_recognized_props(item)
        if not props and not notes and not issues:
            narrative += 1
            continue
        recognized = "; ".join(props) if props else "механика не распознана"
        lines.append(f"- {item.id} — {item.name}: распознано: {recognized}")
        for note in notes:
            label = _KIND_LABELS[note.kind].lower()
            lines.append(f"  - под вопросом ({label}) `{note.path}`: {note.text}")
        for issue in issues:
            lines.append(f"  - под вопросом (валидация): {issue}")
    if narrative:
        lines.append(f"- нарративных предметов без механики: {narrative}")
    return lines


def render_report(result: ImportResult, *, source_name: str, language: str) -> str:
    """Markdown-отчёт на русском: сущности, проблемы, заметки, стоимость, что дальше."""
    scenario = result.draft.scenario
    slug = Path(source_name).stem
    lines = [
        f"# Отчёт импорта: {source_name}",
        "",
        f"- Исходный файл: {source_name}",
        f"- Язык: {language}",
        "",
        "## Сущности",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in entity_counts(scenario).items())
    lines.extend(["", "## Проблемы валидации", ""])
    if result.issues:
        lines.extend(f"- {issue}" for issue in result.issues)
    else:
        lines.append("ссылок-проблем нет")
    lines.extend(["", "## Свойства предметов", ""])
    lines.extend(_item_property_lines(result))
    lines.extend(["", "## Заметки модели", ""])
    if result.draft.notes:
        for kind, label in _KIND_LABELS.items():
            notes = [note for note in result.draft.notes if note.kind == kind]
            if not notes:
                continue
            lines.append(f"### {label}")
            lines.append("")
            lines.extend(f"- `{note.path}` — {note.text}" for note in notes)
            lines.append("")
        # Убрать пустую строку перед следующим разделом.
        lines.pop()
    else:
        lines.append("заметок нет")
    lines.extend(
        [
            "",
            "## Вызовы модели",
            "",
            f"- Вызовов: {len(result.responses)}",
            f"- Стоимость: ${result.cost}",
            "",
            "## Что дальше",
            "",
            f"1. Проверить и поправить YAML: `{slug}.{language}.yaml`.",
            f"2. Положить его в `content/scenarios/{slug}.{language}.yaml`.",
            f"3. Опубликовать: `ai-dnd-play seed --scenario {slug} --lang {language}`.",
            f"4. Играть: `ai-dnd-play play --scenario {slug} --lang {language} --from-db`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_result(
    out_dir: Path,
    slug: str,
    result: ImportResult,
    *,
    source_name: str,
    language: str,
) -> tuple[Path, Path]:
    """Записать ``<slug>.<language>.yaml`` и ``<slug>.<language>.report.md``, вернуть пути.

    YAML проходит round-trip: ``Scenario.model_validate(yaml.safe_load(...))`` даёт
    тот же сценарий, что и черновик (проверено разовой пробой подэтапа 2.5).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = out_dir / f"{slug}.{language}.yaml"
    report_path = out_dir / f"{slug}.{language}.report.md"
    yaml_path.write_text(
        yaml.safe_dump(
            result.draft.scenario.model_dump(mode="json", exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        render_report(result, source_name=source_name, language=language), encoding="utf-8"
    )
    return yaml_path, report_path
