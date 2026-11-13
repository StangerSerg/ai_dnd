"""Ошибки движка правил.

Наружу уходят код и параметры, а не готовый текст: сообщение для пользователя
собирает и переводит фронтенд.
"""


class RulesError(Exception):
    """Базовая ошибка движка правил."""

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        details = ", ".join(f"{name}={value!r}" for name, value in params.items())
        super().__init__(f"{code}: {details}" if details else code)


class DiceNotationError(RulesError):
    """Запись броска не удалось разобрать или она выходит за допустимые пределы."""


class ActionError(RulesError):
    """Действие нельзя выполнить: нет такого участника, навыка, ресурса и так далее.

    Это не сбой, а ответ движка «так нельзя» — его получает тот, кто предложил действие:
    игрок, живой ведущий или ИИ.
    """
