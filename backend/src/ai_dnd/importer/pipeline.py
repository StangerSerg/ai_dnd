"""Конвейер импорта: текст модуля → структурированный черновик → проверка ссылок.

Один structured-вызов задачи ``import`` (шлюз сам проверяет JSON по схеме и просит
модель исправить ошибки). Затем детерминированная проверка ссылок тем же валидатором,
что и при публикации: проблемы не скрываются, а попадают в отчёт автору вместе с
заметками модели. Черновик не публикуется — автор правит YAML и вызывает ``seed``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from ai_dnd.ai.gateway import LLMGateway
from ai_dnd.ai.schemas import LLMResponse, Message
from ai_dnd.importer.prompts import import_system_prompt, import_user_message
from ai_dnd.importer.schema import ImportDraft
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary
from ai_dnd.scenarios.service import ScenarioIssue, find_scenario_issues

# Предел одного вызова: больше — модуль надо делить (тестовый долг 2.5).
MAX_IMPORT_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class ImportResult:
    draft: ImportDraft
    issues: tuple[ScenarioIssue, ...]
    responses: tuple[LLMResponse, ...]

    @property
    def cost(self) -> Decimal:
        return sum((response.cost for response in self.responses), Decimal(0))


async def import_scenario(
    gateway: LLMGateway,
    *,
    text: str,
    language: str,
    ruleset: Ruleset,
    bestiaries: Iterable[Bestiary],
    sheet_template: str,
    class_pack: str | None = None,
) -> ImportResult:
    if not text.strip():
        raise ValueError("empty_document: текст модуля пуст")
    if len(text) > MAX_IMPORT_CHARS:
        raise ValueError(
            f"import_text_too_large: {len(text)} символов, предел {MAX_IMPORT_CHARS}; "
            "разделите модуль на части"
        )
    libraries = tuple(bestiaries)
    # Пакет определяется параметром импорта или явной картой правил, не ответом LLM.
    target_pack = class_pack or {
        "dnd5e-srd@1": "fantasy-core",
        "worms-space@1": "worms-ship",
    }.get(ruleset.ref)
    if target_pack is None:
        raise ValueError(f"Укажите class_pack для набора правил {ruleset.ref}")
    messages = [
        Message.system(import_system_prompt(language)),
        Message.user(
            import_user_message(
                text,
                language=language,
                ruleset=ruleset,
                bestiaries=libraries,
                sheet_template=sheet_template,
            )
        ),
    ]
    structured = await gateway.structured("import", messages, ImportDraft)
    raw_scenario = structured.value.scenario
    info = raw_scenario.scenario.model_copy(
        update={"class_pack": target_pack, "available_classes": ()}
    )
    scenario = raw_scenario.model_copy(update={"scenario": info})
    issues = list(find_scenario_issues(scenario, libraries, ruleset))
    if scenario.scenario.language != language:
        issues.append(
            ScenarioIssue(
                "language_mismatch",
                "scenario.language",
                {"expected": language, "actual": scenario.scenario.language},
            )
        )
    if scenario.scenario.sheet_template != sheet_template:
        issues.append(
            ScenarioIssue(
                "template_mismatch",
                "scenario.sheet_template",
                {"expected": sheet_template, "actual": scenario.scenario.sheet_template},
            )
        )
    return ImportResult(
        draft=structured.value.model_copy(update={"scenario": scenario}),
        issues=tuple(issues),
        responses=structured.responses,
    )
