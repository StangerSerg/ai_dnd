"""Загрузка набора правил и проверка, что шаблон карточки к нему подходит."""

from pathlib import Path

from ai_dnd.rules.ruleset import Ruleset, RulesetError, find_ruleset_issues
from ai_dnd.sheets.issues import SheetIssue
from ai_dnd.sheets.schemas import SheetTemplate


def load_ruleset(path: Path) -> Ruleset:
    """Прочитать набор правил из JSON-файла и проверить его.

    Если набор составлен неверно — ``RulesetError`` со всеми проблемами.
    """
    ruleset = Ruleset.model_validate_json(path.read_text(encoding="utf-8"))
    issues = find_ruleset_issues(ruleset)
    if issues:
        raise RulesetError(issues)
    return ruleset


def find_compatibility_issues(template: SheetTemplate, ruleset: Ruleset) -> list[SheetIssue]:
    """Чего шаблону карточки не хватает, чтобы играть по этому набору правил.

    Пустой список — персонаж по такому шаблону сможет делать все проверки и спасброски
    набора, а если в наборе есть бой — участвовать в нём.
    """
    issues: list[SheetIssue] = []
    bindings = template.bindings

    if template.ruleset is not None and template.ruleset != ruleset.ref:
        issues.append(
            SheetIssue(
                "ruleset_mismatch", "ruleset", {"expected": template.ruleset, "actual": ruleset.ref}
            )
        )

    for check_id, check in ruleset.checks.items():
        # Навык без своей привязки считается по характеристике — её привязки достаточно.
        if check_id not in bindings.checks and check.ability not in bindings.checks:
            issues.append(SheetIssue("missing_check", "bindings.checks", {"check": check_id}))
    issues.extend(
        SheetIssue("unknown_check", f"bindings.checks.{check_id}")
        for check_id in bindings.checks
        if check_id not in ruleset.checks
    )

    issues.extend(
        SheetIssue("missing_save", "bindings.saves", {"save": save_id})
        for save_id in ruleset.saves
        if save_id not in bindings.saves
    )
    issues.extend(
        SheetIssue("unknown_save", f"bindings.saves.{save_id}")
        for save_id in bindings.saves
        if save_id not in ruleset.saves
    )

    if ruleset.combat.enabled:
        # Без хитов и класса доспеха по персонажу нельзя ни попасть, ни нанести урон.
        if bindings.hp is None:
            issues.append(SheetIssue("missing_binding", "bindings.hp"))
        if bindings.ac is None:
            issues.append(SheetIssue("missing_binding", "bindings.ac"))
    return issues
