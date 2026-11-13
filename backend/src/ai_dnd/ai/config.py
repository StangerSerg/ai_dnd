"""Конфигурация ИИ: провайдеры, модели с ценами, настройки задач.

Читается из YAML-файла (по умолчанию ``backend/config/llm.yaml``). Названия моделей
и цены живут там, а не в коде: сменился тариф — правится одна строка.
"""

from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_dnd.ai.schemas import Usage

TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Prices(_Model):
    """Цены в долларах за 1 млн токенов."""

    input: Annotated[Decimal, Field(ge=0)]
    cached_input: Annotated[Decimal, Field(ge=0)]
    output: Annotated[Decimal, Field(ge=0)]

    def cost(self, usage: Usage) -> Decimal:
        """Стоимость вызова: вход из кэша и обычный вход считаются по разным ценам."""
        fresh_input = max(0, usage.input_tokens - usage.cached_input_tokens)
        total = (
            fresh_input * self.input
            + usage.cached_input_tokens * self.cached_input
            + usage.output_tokens * self.output
        )
        return total / TOKENS_PER_PRICE_UNIT


class TimeWindow(_Model):
    start: time = Field(alias="from")
    end: time = Field(alias="to")


class ProviderConfig(_Model):
    base_url: str
    # Часы повышенного тарифа, UTC, по будням. Пусто — тариф один.
    peak_hours: tuple[TimeWindow, ...] = ()

    def is_peak(self, moment: datetime) -> bool:
        moment = moment.astimezone(UTC)
        if moment.weekday() >= 5:
            return False
        now = moment.time()
        return any(window.start <= now < window.end for window in self.peak_hours)


class ModelPrices(_Model):
    off_peak: Prices
    # Не задано — цена не зависит от времени суток.
    peak: Prices | None = None


class ModelConfig(_Model):
    provider: str
    prices: ModelPrices


class TaskConfig(_Model):
    model: str
    thinking: bool = False
    reasoning_effort: Literal["low", "high", "max"] | None = None
    max_tokens: Annotated[int, Field(ge=1)]
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    timeout: Annotated[float, Field(gt=0)] = 60.0


class RetryConfig(_Model):
    attempts: Annotated[int, Field(ge=1, le=10)] = 3
    backoff_seconds: Annotated[float, Field(ge=0)] = 1.0
    validation_attempts: Annotated[int, Field(ge=0, le=5)] = 2


class LLMConfig(_Model):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    tasks: dict[str, TaskConfig]
    retries: RetryConfig = Field(default_factory=RetryConfig)

    @model_validator(mode="after")
    def _references_exist(self) -> Self:
        for name, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(f"модель «{name}»: неизвестный провайдер «{model.provider}»")
        for name, task in self.tasks.items():
            if task.model not in self.models:
                raise ValueError(f"задача «{name}»: неизвестная модель «{task.model}»")
        return self

    def prices_for(self, model: str, moment: datetime) -> Prices:
        """Цены модели на этот момент: в часы пик у провайдера может быть другой тариф."""
        config = self.models[model]
        if config.prices.peak is not None and self.providers[config.provider].is_peak(moment):
            return config.prices.peak
        return config.prices.off_peak


def load_llm_config(path: Path) -> LLMConfig:
    return LLMConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
