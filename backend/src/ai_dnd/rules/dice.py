"""Кости: разбор записи броска и сам бросок.

Понимает ``2d6+3``, ``d20``, ``4d6kh3`` (оставить три старших), ``2d20kl1`` (оставить
младшую), ``d%``, русскую запись ``2к6+3`` и пометку преимущества или помехи в конце:
``1d20+5 adv``, ``1к20 помеха``.

Преимущество и помеха работают для любых костей, не только для d20: к первой группе
костей добавляется одна кость, и лишняя (младшая или старшая) отбрасывается.
``1d20`` с преимуществом — это ``2d20kh1``, ``2d6`` с помехой — ``3d6kl2``.

    >>> from ai_dnd.rules.rng import ScriptedRng
    >>> str(roll("2к6+3", ScriptedRng([4, 2])))
    '2d6 + 3 → [4, 2] + 3 = 9'
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ai_dnd.rules.errors import DiceNotationError
from ai_dnd.rules.rng import Rng, SystemRng

# Пределы защищают от записей вроде 999999d999999: бросок не должен «повесить» сервер.
MAX_NOTATION_LENGTH = 100
MAX_TERMS = 20
MAX_DICE_PER_TERM = 100
MIN_SIDES = 2
MAX_SIDES = 1000
MAX_CONSTANT = 1_000_000

type Sign = Literal[1, -1]


class RollMode(StrEnum):
    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


class KeepMode(StrEnum):
    HIGHEST = "kh"
    LOWEST = "kl"


@dataclass(frozen=True, slots=True)
class Keep:
    """Сколько костей из группы идёт в сумму: старшие или младшие."""

    mode: KeepMode
    count: int


@dataclass(frozen=True, slots=True)
class DiceTerm:
    """Группа одинаковых костей: ``2d6``, ``4d6kh3``."""

    count: int
    sides: int
    sign: Sign = 1
    keep: Keep | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.count <= MAX_DICE_PER_TERM:
            raise DiceNotationError(
                "dice_count_out_of_range", value=self.count, min=1, max=MAX_DICE_PER_TERM
            )
        if not MIN_SIDES <= self.sides <= MAX_SIDES:
            raise DiceNotationError(
                "dice_sides_out_of_range", value=self.sides, min=MIN_SIDES, max=MAX_SIDES
            )
        if self.keep is not None and not 1 <= self.keep.count <= self.count:
            raise DiceNotationError(
                "keep_out_of_range", value=self.keep.count, min=1, max=self.count
            )

    @property
    def kept_count(self) -> int:
        """Сколько костей группы идёт в сумму."""
        return self.count if self.keep is None else self.keep.count

    @property
    def notation(self) -> str:
        """Запись группы без знака: ``2d20kh1``."""
        suffix = "" if self.keep is None else f"{self.keep.mode.value}{self.keep.count}"
        return f"{self.count}d{self.sides}{suffix}"


@dataclass(frozen=True, slots=True)
class ConstantTerm:
    """Число-модификатор. ``label`` поясняет, откуда оно взялось («dex_mod»)."""

    value: int
    sign: Sign = 1
    label: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.value <= MAX_CONSTANT:
            raise DiceNotationError(
                "constant_out_of_range", value=self.value, min=0, max=MAX_CONSTANT
            )

    @classmethod
    def signed(cls, value: int, label: str | None = None) -> "ConstantTerm":
        """Модификатор из числа со знаком: ``signed(-1)`` — это «минус один»."""
        return cls(value=abs(value), sign=-1 if value < 0 else 1, label=label)


type Term = DiceTerm | ConstantTerm


@dataclass(frozen=True, slots=True)
class DiceExpression:
    """Разобранная запись броска: слагаемые и режим (обычный, преимущество, помеха)."""

    terms: tuple[Term, ...]
    mode: RollMode = RollMode.NORMAL

    def __post_init__(self) -> None:
        if not self.terms:
            raise DiceNotationError("empty")
        if len(self.terms) > MAX_TERMS:
            raise DiceNotationError("too_many_terms", value=len(self.terms), max=MAX_TERMS)

    def __str__(self) -> str:
        """Каноническая запись: ``2d6 + 3``, ``1d20 + 5 adv``."""
        parts = [
            _signed_part(index, term.sign, _term_notation(term))
            for index, term in enumerate(self.terms)
        ]
        return "".join(parts) + _MODE_SUFFIX[self.mode]

    def resolve(self, mode: RollMode | None = None) -> "DiceExpression":
        """Выражение, в котором преимущество или помеха уже превращены в лишнюю кость.

        ``mode`` заменяет режим из записи. Меняется первая группа костей; если костей
        в выражении нет, режим ни на что не влияет.
        """
        mode = self.mode if mode is None else mode
        if mode is RollMode.NORMAL:
            return replace(self, mode=RollMode.NORMAL)

        for index, term in enumerate(self.terms):
            if not isinstance(term, DiceTerm):
                continue
            if term.keep is not None:
                # «4d6kh3 с помехой» можно понять по-разному, поэтому не угадываем.
                raise DiceNotationError("mode_with_keep", term=term.notation, mode=mode.value)
            keep_mode = KeepMode.HIGHEST if mode is RollMode.ADVANTAGE else KeepMode.LOWEST
            boosted = replace(term, count=term.count + 1, keep=Keep(keep_mode, term.count))
            terms = (*self.terms[:index], boosted, *self.terms[index + 1 :])
            return DiceExpression(terms=terms)
        return replace(self, mode=RollMode.NORMAL)

    def bounds(self, mode: RollMode | None = None) -> tuple[int, int]:
        """Наименьший и наибольший возможный итог броска."""
        low = high = 0
        for term in self.resolve(mode).terms:
            if isinstance(term, DiceTerm):
                term_low, term_high = term.kept_count, term.kept_count * term.sides
            else:
                term_low = term_high = term.value
            if term.sign < 0:
                term_low, term_high = -term_high, -term_low
            low += term_low
            high += term_high
        return low, high


# --- Результат броска ---


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class DieRoll(_Frozen):
    """Одна кость: что выпало и идёт ли она в сумму."""

    value: int
    kept: bool = True


class DiceTermResult(_Frozen):
    kind: Literal["dice"] = "dice"
    sign: Sign
    notation: str
    sides: int
    rolls: tuple[DieRoll, ...]
    # Вклад в итог, уже со знаком.
    subtotal: int


class ConstantTermResult(_Frozen):
    kind: Literal["constant"] = "constant"
    sign: Sign
    value: int
    label: str | None = None
    # Вклад в итог, уже со знаком.
    subtotal: int


TermResult = Annotated[DiceTermResult | ConstantTermResult, Field(discriminator="kind")]


class RollResult(_Frozen):
    """Бросок со всеми подробностями: его целиком можно положить в событие ``DiceRolled``."""

    # Каноническая запись того, что бросили на самом деле: с преимуществом это 2d20kh1.
    notation: str
    mode: RollMode
    terms: tuple[TermResult, ...]
    total: int

    @property
    def dice_total(self) -> int:
        """Сумма по костям, без модификаторов."""
        return sum(term.subtotal for term in self.terms if isinstance(term, DiceTermResult))

    @property
    def modifier_total(self) -> int:
        """Сумма модификаторов, без костей."""
        return sum(term.subtotal for term in self.terms if isinstance(term, ConstantTermResult))

    @property
    def natural(self) -> int | None:
        """«Чистое» значение первой группы костей, без модификаторов: 20 на d20 — это оно.

        ``None``, если костей в броске не было.
        """
        for term in self.terms:
            if isinstance(term, DiceTermResult):
                return sum(die.value for die in term.rolls if die.kept)
        return None

    def __str__(self) -> str:
        """Строка для логов и консоли: ``2d20kh1 + 5 → [17, (4)] + 5 = 22``.

        Отброшенные кости показаны в скобках.
        """
        parts = [
            _signed_part(index, term.sign, _term_result_body(term))
            for index, term in enumerate(self.terms)
        ]
        return f"{self.notation} → {''.join(parts)} = {self.total}"


# --- Разбор записи ---

_MODE_WORDS = {
    "adv": RollMode.ADVANTAGE,
    "advantage": RollMode.ADVANTAGE,
    "преим": RollMode.ADVANTAGE,
    "преимущество": RollMode.ADVANTAGE,
    "dis": RollMode.DISADVANTAGE,
    "disadvantage": RollMode.DISADVANTAGE,
    "помеха": RollMode.DISADVANTAGE,
}

_MODE_SUFFIX = {
    RollMode.NORMAL: "",
    RollMode.ADVANTAGE: " adv",
    RollMode.DISADVANTAGE: " dis",
}

# Буква кости: латинская d, русские «к» (2к6) и «д» (2д6). Минус — в том числе
# типографский: записи часто копируют из текста.
_TOKEN = re.compile(
    r"""
      (?P<dice>(?P<count>[0-9]*)[dдк](?P<sides>[0-9]+|%)(?:(?P<keep>k[hl])(?P<keep_count>[0-9]*))?)
    | (?P<number>[0-9]+)
    | (?P<sign>[+\-−–])
    | (?P<word>[a-zа-яё]+)
    """,
    re.VERBOSE,
)


def parse(notation: str) -> DiceExpression:
    """Разобрать запись броска. Регистр и пробелы между слагаемыми не важны."""
    if len(notation) > MAX_NOTATION_LENGTH:
        raise DiceNotationError("too_long", length=len(notation), max=MAX_NOTATION_LENGTH)

    text = notation.lower()
    terms: list[Term] = []
    mode: RollMode | None = None
    pending_sign: Sign | None = None
    position = 0

    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(text, position)
        if match is None:
            raise DiceNotationError(
                "unexpected_character", character=text[position], position=position
            )
        token = match.group()
        if mode is not None:
            # Пометка преимущества или помехи должна быть последней.
            raise DiceNotationError("unexpected_token", token=token, position=position)

        if match.group("sign"):
            if pending_sign is not None:
                raise DiceNotationError("unexpected_token", token=token, position=position)
            pending_sign = 1 if token == "+" else -1
        elif match.group("word"):
            if token not in _MODE_WORDS:
                raise DiceNotationError("unknown_word", word=token, position=position)
            if pending_sign is not None or not terms:
                raise DiceNotationError("unexpected_token", token=token, position=position)
            mode = _MODE_WORDS[token]
        else:
            if terms and pending_sign is None:
                # Два слагаемых подряд без знака: «2d6 3».
                raise DiceNotationError("unexpected_token", token=token, position=position)
            terms.append(_term_from_match(match, pending_sign or 1))
            pending_sign = None
        position = match.end()

    if pending_sign is not None:
        raise DiceNotationError("unexpected_end")
    if not terms:
        raise DiceNotationError("empty")
    return DiceExpression(terms=tuple(terms), mode=mode or RollMode.NORMAL)


def _term_from_match(match: re.Match[str], sign: Sign) -> Term:
    if match.group("number"):
        return ConstantTerm(value=int(match.group("number")), sign=sign)

    sides = match.group("sides")
    keep: Keep | None = None
    if match.group("keep"):
        # «kh» без числа — оставить одну кость.
        keep = Keep(KeepMode(match.group("keep")), int(match.group("keep_count") or 1))
    return DiceTerm(
        count=int(match.group("count") or 1),
        sides=100 if sides == "%" else int(sides),
        sign=sign,
        keep=keep,
    )


def _validated_notation(notation: str) -> str:
    try:
        parse(notation)
    except DiceNotationError as error:
        # Pydantic превращает ValueError в ошибку проверки поля.
        raise ValueError(str(error)) from error
    return notation


# Запись броска как поле Pydantic-модели: проверяется при загрузке данных.
DiceNotation = Annotated[str, AfterValidator(_validated_notation)]


# --- Бросок ---

_system_rng = SystemRng()


def roll(
    expression: str | DiceExpression,
    rng: Rng | None = None,
    *,
    mode: RollMode | None = None,
) -> RollResult:
    """Бросить кости.

    ``rng`` — источник случайности; по умолчанию генератор операционной системы.
    Движок правил всегда передаёт его явно, чтобы тесты были повторяемыми.
    ``mode`` заменяет режим, указанный в записи (``adv`` / ``dis``).
    """
    parsed = parse(expression) if isinstance(expression, str) else expression
    effective_mode = parsed.mode if mode is None else mode
    resolved = parsed.resolve(effective_mode)
    rng = rng or _system_rng

    results: list[DiceTermResult | ConstantTermResult] = []
    for term in resolved.terms:
        if isinstance(term, DiceTerm):
            results.append(_roll_dice_term(term, rng))
        else:
            results.append(
                ConstantTermResult(
                    sign=term.sign,
                    value=term.value,
                    label=term.label,
                    subtotal=term.sign * term.value,
                )
            )

    return RollResult(
        notation=str(resolved),
        mode=effective_mode,
        terms=tuple(results),
        total=sum(result.subtotal for result in results),
    )


def _roll_dice_term(term: DiceTerm, rng: Rng) -> DiceTermResult:
    values = [rng.randint(1, term.sides) for _ in range(term.count)]
    kept = _kept_indexes(values, term.keep)
    rolls = tuple(DieRoll(value=value, kept=index in kept) for index, value in enumerate(values))
    return DiceTermResult(
        sign=term.sign,
        notation=term.notation,
        sides=term.sides,
        rolls=rolls,
        subtotal=term.sign * sum(roll.value for roll in rolls if roll.kept),
    )


def _kept_indexes(values: list[int], keep: Keep | None) -> set[int]:
    if keep is None:
        return set(range(len(values)))
    # Сортировка устойчивая: из одинаковых значений остаётся кость, брошенная раньше.
    order = sorted(
        range(len(values)),
        key=lambda index: values[index],
        reverse=keep.mode is KeepMode.HIGHEST,
    )
    return set(order[: keep.count])


# --- Вывод ---


def _signed_part(index: int, sign: Sign, body: str) -> str:
    if index == 0:
        return f"-{body}" if sign < 0 else body
    return f" - {body}" if sign < 0 else f" + {body}"


def _term_notation(term: Term) -> str:
    return term.notation if isinstance(term, DiceTerm) else str(term.value)


def _term_result_body(term: DiceTermResult | ConstantTermResult) -> str:
    if isinstance(term, ConstantTermResult):
        return str(term.value)
    values = ", ".join(str(roll.value) if roll.kept else f"({roll.value})" for roll in term.rolls)
    return f"[{values}]"
