"""Генераторы случайных чисел для движка правил.

Движок не обращается к ``random`` напрямую: генератор всегда передаётся снаружи.
В работе это ``SystemRng``, в тестах — ``SeededRng`` (повторяемая последовательность)
или ``ScriptedRng`` (заранее заданные значения).
"""

import random
import secrets
from collections.abc import Iterable, Sequence
from typing import Protocol, TypeVar

_T = TypeVar("_T")


class Rng(Protocol):
    """Источник случайных целых чисел."""

    def randint(self, low: int, high: int) -> int:
        """Случайное целое из отрезка [low, high], границы включены."""
        ...

    def choice(self, options: Sequence[_T]) -> _T:
        """Случайный элемент непустой последовательности."""
        ...


class SystemRng:
    """Генератор операционной системы: результат нельзя предсказать или повторить."""

    def __init__(self) -> None:
        self._random = secrets.SystemRandom()

    def randint(self, low: int, high: int) -> int:
        return self._random.randint(low, high)

    def choice(self, options: Sequence[_T]) -> _T:
        if not options:
            raise ValueError("choice из пустой последовательности")
        if len(options) == 1:
            return options[0]
        return options[self.randint(0, len(options) - 1)]


class SeededRng:
    """Повторяемый генератор: один и тот же seed даёт одну и ту же последовательность."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._random = random.Random(seed)

    def randint(self, low: int, high: int) -> int:
        return self._random.randint(low, high)

    def choice(self, options: Sequence[_T]) -> _T:
        if not options:
            raise ValueError("choice из пустой последовательности")
        # Единственный вариант не тратит бросок: находка гарантирована.
        if len(options) == 1:
            return options[0]
        return options[self.randint(0, len(options) - 1)]


class RngExhaustedError(RuntimeError):
    """У ``ScriptedRng`` закончились заготовленные значения."""


class ScriptedRng:
    """Генератор, который выдаёт заранее заданные значения по порядку.

    Нужен, чтобы в тесте получить конкретный бросок («на d20 выпало 17»).
    """

    def __init__(self, values: Iterable[int]) -> None:
        self._values = list(values)
        self._position = 0

    @property
    def remaining(self) -> int:
        """Сколько заготовленных значений ещё не использовано."""
        return len(self._values) - self._position

    def randint(self, low: int, high: int) -> int:
        if self._position >= len(self._values):
            raise RngExhaustedError(
                f"Заготовленные значения закончились: запрошено число из [{low}, {high}]"
            )
        value = self._values[self._position]
        if not low <= value <= high:
            raise ValueError(
                f"Заготовленное значение {value} (№{self._position + 1}) "
                f"не входит в [{low}, {high}]"
            )
        self._position += 1
        return value

    def choice(self, options: Sequence[_T]) -> _T:
        if not options:
            raise ValueError("choice из пустой последовательности")
        if len(options) == 1:
            return options[0]
        return options[self.randint(0, len(options) - 1)]
