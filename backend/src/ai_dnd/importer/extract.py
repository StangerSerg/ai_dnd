"""Извлечение текста из файла сценария (подэтап 2.5).

Markdown/TXT, DOCX и PDF → plain text (Markdown-подобный) для отправки в LLM.
Чистый файловый ввод-вывод: без сети, базы данных и обращений к модели.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PasswordType, PdfReader
from pypdf.errors import PdfReadError

# WHY: защита от мусорного входа до парсинга; предел текста 60k символов проверяет pipeline.
MAX_FILE_BYTES = 5_000_000

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".docx", ".pdf")
_HEADING_RE = re.compile(r"^(?:Heading|Заголовок)\s+(\d+)$")
_NUMPR_PATH = f"{qn('w:pPr')}/{qn('w:numPr')}"


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """Текст сценария и формат исходного файла."""

    text: str
    source_format: Literal["markdown", "docx", "pdf"]


def extract_text(path: Path) -> ExtractedText:
    """Извлекает текст сценария из .md/.markdown/.txt, .docx или .pdf.

    Бросает ValueError при неподдерживаемом расширении, пустом тексте, файле
    больше MAX_FILE_BYTES (file_too_large) или битом файле (invalid_document);
    несуществующий файл даёт FileNotFoundError.
    """
    # Дешёвый pre-check размера до чтения и парсинга файла.
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        msg = f"file_too_large: {path} ({size} байт, лимит {MAX_FILE_BYTES})"
        raise ValueError(msg)
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        text = _extract_markdown(path)
        source_format: Literal["markdown", "docx", "pdf"] = "markdown"
    elif suffix == ".docx":
        try:
            text = _extract_docx(path)
        except (PackageNotFoundError, zipfile.BadZipFile) as error:
            msg = f"invalid_document: {path} не является корректным DOCX"
            raise ValueError(msg) from error
        source_format = "docx"
    elif suffix == ".pdf":
        try:
            text = _extract_pdf(path)
        except PdfReadError as error:
            msg = f"invalid_document: {path} не является корректным PDF"
            raise ValueError(msg) from error
        source_format = "pdf"
    else:
        supported = ", ".join(_SUPPORTED_SUFFIXES)
        msg = f"unsupported_format: {suffix or path.name!r}; поддерживаются: {supported}"
        raise ValueError(msg)
    text = text.strip()
    if not text:
        msg = f"empty_document: {path}"
        raise ValueError(msg)
    return ExtractedText(text=text, source_format=source_format)


def _extract_markdown(path: Path) -> str:
    """Читает текст в utf-8-sig и нормализует переносы строк к \\n."""
    raw = path.read_text(encoding="utf-8-sig")
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _extract_docx(path: Path) -> str:
    """Обходит тело DOCX в порядке следования параграфов и таблиц."""
    document = Document(str(path))
    blocks: list[str] = []
    # Document.paragraphs пропускает таблицы, поэтому идём по XML тела напрямую.
    for element in document.element.body:
        if element.tag == qn("w:p"):
            block = _paragraph_text(Paragraph(cast(CT_P, element), document))
        elif element.tag == qn("w:tbl"):
            block = _table_markdown(Table(cast(CT_Tbl, element), document))
        else:
            continue
        if block:
            blocks.append(block)
    # Пустые параграфы отброшены: "\n\n" между блоками даёт не более одного разделителя.
    return "\n\n".join(blocks)


def _paragraph_text(paragraph: Paragraph) -> str:
    """Параграф → строка: заголовок стиля — решётки, список — «- », иначе текст как есть."""
    text = paragraph.text.strip()
    if not text:
        return ""
    # У style.name в python-docx нет аннотаций: getattr + cast, чтобы не звать нетипизированный код.
    style_name = cast("str | None", getattr(paragraph.style, "name", None))
    heading = _HEADING_RE.match(style_name) if style_name else None
    if heading is not None:
        level = min(max(int(heading.group(1)), 1), 6)
        return f"{'#' * level} {text}"
    if _is_list_item(paragraph, style_name):
        return f"- {text}"
    return text


def _is_list_item(paragraph: Paragraph, style_name: str | None) -> bool:
    """Дешёвое распознавание списка: нумерация в XML или стиль «List …»/«Список»."""
    if paragraph._p.find(_NUMPR_PATH) is not None:
        return True
    return style_name is not None and (
        style_name.startswith("List") or style_name.startswith("Список")
    )


def _table_markdown(table: Table) -> str:
    """Таблица DOCX → Markdown-таблица; переносы строк в ячейках схлопываются в пробел."""
    rows = [
        [" ".join(cell.text.split()).replace("|", "\\|") for cell in row.cells]
        for row in table.rows
    ]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    lines = [_md_row(rows[0], width), "|" + "---|" * width]
    lines.extend(_md_row(row, width) for row in rows[1:])
    return "\n".join(lines)


def _md_row(cells: list[str], width: int) -> str:
    """Строка Markdown-таблицы, дополненная пустыми ячейками до ширины таблицы."""
    padded = [*cells, *([""] * (width - len(cells)))]
    return "| " + " | ".join(padded) + " |"


def _extract_pdf(path: Path) -> str:
    """Текст всех страниц PDF через pypdf; страницы соединяются пустой строкой."""
    reader = PdfReader(path)
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        msg = f"encrypted_pdf: {path} защищён паролем, пустой пароль не подошёл"
        raise ValueError(msg)
    pages = [page.extract_text().rstrip() for page in reader.pages]
    return "\n\n".join(pages)
