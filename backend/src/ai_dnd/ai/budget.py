"""Консервативный preflight полного запроса без внешнего токенизатора модели."""

import json

from ai_dnd.ai.config import BudgetConfig
from ai_dnd.ai.provider import CompletionRequest, LLMError


def estimate_input_tokens(request: CompletionRequest) -> int:
    """Верхняя защитная оценка: UTF-8 байты JSON и запас на служебные токены.

    Это НЕ точный tokenizer DeepSeek. Байтовая оценка намеренно завышает
    обычный текст; framing и специальные токены добавлены отдельно. Формат
    провайдера может измениться, поэтому порог остаётся конфигурацией приложения.
    """
    payload = {
        "model": request.model,
        "messages": [message.model_dump(mode="json") for message in request.messages],
        "tools": [tool.model_dump(mode="json") for tool in request.tools],
        "tool_choice": request.tool_choice,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "thinking": request.thinking,
        "reasoning_effort": request.reasoning_effort,
        "json_output": request.json_output,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(encoded) + 64 + 16 * len(request.messages) + 32 * len(request.tools)


def check_budget(request: CompletionRequest, budget: BudgetConfig) -> None:
    estimate = estimate_input_tokens(request)
    reserve = request.max_tokens + budget.output_margin_tokens
    if estimate + reserve > budget.window_tokens:
        raise LLMError(
            "context_budget_exceeded",
            f"estimated_input={estimate}, output_reserve={reserve}, "
            f"application_window={budget.window_tokens}",
        )
