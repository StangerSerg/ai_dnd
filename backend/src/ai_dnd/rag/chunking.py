"""Нарезка сущностей сценария на фрагменты для поиска (архитектура 8.4).

Фрагмент — кусок текста с метаданными: тип сущности, ключ, сцена, видимость, язык.
Видимость ставится на уровне текста, а не сущности целиком: у сцены ``read_aloud``
видят игроки, а ``dm_notes`` — только ведущий, поэтому это разные фрагменты. Так
поиск для игрока не достаёт секреты ведущего (архитектура 9.2).

Длинный текст режется на куски примерно по 200–500 токенов.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ai_dnd.i18n import localize

# Ориентир архитектуры: 200–500 токенов на фрагмент. Для русского и английского
# это примерно 1200 символов; режем по абзацам и предложениям, не посередине слова.
MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True)
class Chunk:
    type: str
    key: str
    scene_id: str | None
    visibility: str
    language: str
    text: str
    ord: int


def _split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Куски текста не длиннее ``max_chars``, по границам абзацев и предложений."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in _sentences(paragraph, max_chars):
            if current and len(current) + len(sentence) + 1 > max_chars:
                pieces.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _sentences(paragraph: str, max_chars: int) -> Iterator[str]:
    """Абзац целиком, если влезает; иначе по предложениям; иначе жёстко по длине."""
    if len(paragraph) <= max_chars:
        yield paragraph
        return
    buf = ""
    for token in paragraph.replace("! ", "!|").replace("? ", "?|").split("|"):
        token = token.strip()
        if not token:
            continue
        if len(token) > max_chars:
            if buf:
                yield buf
                buf = ""
            for start in range(0, len(token), max_chars):
                yield token[start : start + max_chars]
        elif buf and len(buf) + len(token) + 1 > max_chars:
            yield buf
            buf = token
        else:
            buf = f"{buf} {token}".strip()
    if buf:
        yield buf


def _join(parts: list[str | None]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _text(value: Any, language: str) -> str | None:
    """Текст поля на языке версии: строка как есть, словарь языков — через localize."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value:
        return localize(value, language)
    return None


def _entity_parts(type_: str, data: dict[str, Any], language: str) -> list[tuple[str, str]]:
    """Пары (видимость, текст) для сущности. Видимость — на уровне текста."""
    if type_ == "scene":
        public = _join(
            [_text(data.get("title"), language), _text(data.get("read_aloud"), language)]
        )
        checks = data.get("checks") or []
        check_text = _join(
            [
                _text(c.get("description") or c.get("id"), language)
                for c in checks
                if isinstance(c, dict)
            ]
        )
        dm = _join([_text(data.get("dm_notes"), language), check_text])
        return [("public", public), ("dm", dm)]
    if type_ == "npc":
        public = _join(
            [_text(data.get("name"), language), _text(data.get("description"), language)]
        )
        secrets = data.get("secrets") or []
        dm = _join(
            [_text(data.get("personality"), language), *[_text(s, language) for s in secrets]]
        )
        return [("public", public), ("dm", dm)]
    if type_ == "encounter":
        creatures = data.get("creatures") or []
        names = _join(
            [
                _text(c.get("name") or c.get("statblock"), language)
                for c in creatures
                if isinstance(c, dict)
            ]
        )
        text = _join(
            [_text(data.get("title"), language), _text(data.get("description"), language), names]
        )
        return [("dm", text)]
    if type_ == "statblock":
        text = _join([_text(data.get("name"), language), _text(data.get("traits"), language)])
        return [("dm", text)]
    if type_ == "item":
        text = _join([_text(data.get("name"), language), _text(data.get("description"), language)])
        return [(data.get("visibility", "public"), text)]
    if type_ == "clue":
        return [(data.get("visibility", "dm"), _join([_text(data.get("text"), language)]))]
    if type_ == "handout":
        text = _join([_text(data.get("title"), language), _text(data.get("text"), language)])
        return [(data.get("visibility", "dm"), text)]
    if type_ == "lore":
        text = _join([_text(data.get("title"), language), _text(data.get("text"), language)])
        return [(data.get("visibility", "public"), text)]
    # Неизвестный тип — индексируем как есть, только для ведущего.
    return [("dm", _join([_text(v, language) for v in data.values()]))]


def chunk_entity(type_: str, key: str, data: dict[str, Any], language: str) -> list[Chunk]:
    """Фрагменты одной сущности. ``scene_id`` заполняется только у сцен."""
    scene_id = key if type_ == "scene" else None
    chunks: list[Chunk] = []
    ord_ = 0
    for visibility, text in _entity_parts(type_, data, language):
        for piece in _split_text(text):
            chunks.append(
                Chunk(
                    type=type_,
                    key=key,
                    scene_id=scene_id,
                    visibility=visibility,
                    language=language,
                    text=piece,
                    ord=ord_,
                )
            )
            ord_ += 1
    return chunks
