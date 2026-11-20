"""Ошибки биллинга: понятная остановка ИИ и нарушения инвариантов журнала."""


class BillingStop(Exception):  # noqa: N818 — имя задано требованиями подэтапа 3.6
    """ИИ-вызов остановлен до обращения к модели: лимит, предохранитель или баланс.

    Сообщение рассчитано на игрока; человеческая игра не блокируется.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InsufficientCredits(BillingStop):
    """Кредитов не хватает даже на резерв хода."""

    def __init__(self, balance: int, amount: int) -> None:
        super().__init__(
            "insufficient_credits",
            f"Не хватает кредитов для хода ИИ: на балансе {balance}, нужно {amount}. "
            "Бесплатные кредиты обновятся завтра; игра между людьми продолжается.",
        )


class LedgerError(Exception):
    """Нарушение инварианта журнала: двойное списание, возврат сверх резерва и т.п."""
