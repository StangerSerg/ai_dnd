"""Тексты на нескольких языках (архитектура, раздел 18).

Подпись в контенте — либо строка на языке автора, либо словарь по языкам:
``"Сила"`` или ``{"ru": "Сила", "en": "Strength"}``.
"""

from typing import Annotated

from pydantic import Field, StringConstraints

DEFAULT_LANGUAGE = "en"

LanguageCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

LocalizedText = _Text | Annotated[dict[LanguageCode, _Text], Field(min_length=1)]


def localize(text: LocalizedText, language: str, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Текст на нужном языке; если перевода нет — на запасном, иначе на любом."""
    if isinstance(text, str):
        return text
    if language in text:
        return text[language]
    if fallback in text:
        return text[fallback]
    return next(iter(text.values()))
