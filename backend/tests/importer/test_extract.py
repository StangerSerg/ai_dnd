"""Тесты extract_text (подэтап 2.5): md/txt, DOCX, PDF и ошибки формата."""

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml.parser import OxmlElement
from docx.table import Table
from pypdf import PdfReader, PdfWriter

from ai_dnd.importer.extract import MAX_FILE_BYTES, extract_text

# --- Markdown / TXT ---


def test_markdown_bom_and_crlf(tmp_path: Path) -> None:
    """utf-8-sig съедает BOM, CRLF нормализуется в LF."""
    path = tmp_path / "module.md"
    path.write_bytes("\ufeff# Заголовок\r\n\r\nТекст сценария.\r\n".encode())

    extracted = extract_text(path)

    assert extracted.source_format == "markdown"
    assert extracted.text == "# Заголовок\n\nТекст сценария."
    assert "\r" not in extracted.text
    assert not extracted.text.startswith("\ufeff")


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".txt", ".MD"])
def test_text_suffixes(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"module{suffix}"
    path.write_text("# Модуль\n\nТекст.", encoding="utf-8")

    extracted = extract_text(path)

    assert extracted.source_format == "markdown"
    assert extracted.text == "# Модуль\n\nТекст."


def test_markdown_lone_cr(tmp_path: Path) -> None:
    """Одиночный \\r (старые Mac-переносы) тоже становится \\n."""
    path = tmp_path / "module.txt"
    path.write_bytes("первая\rвторая".encode())

    assert extract_text(path).text == "первая\nвторая"


# --- DOCX ---


def _build_docx(path: Path) -> None:
    """Заголовок → абзац → таблица → абзац → список, как в модуле автора."""
    doc = Document()
    doc.add_paragraph("Глава первая", style="Heading 2")
    doc.add_paragraph("Вступление таверны.")
    table: Table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Имя"
    table.cell(0, 1).text = "Роль"
    table.cell(1, 0).text = "Бром"
    table.cell(1, 1).text = "Трактирщик\nс секретом"
    doc.add_paragraph("После таблицы.")
    doc.add_paragraph("Пункт списка", style="List Bullet")
    doc.save(str(path))


def test_docx_structure(tmp_path: Path) -> None:
    path = tmp_path / "module.docx"
    _build_docx(path)

    extracted = extract_text(path)

    assert extracted.source_format == "docx"
    text = extracted.text
    assert text.split("\n")[0] == "## Глава первая"
    # Порядок тела документа сохранён: заголовок → абзац → таблица → абзац.
    assert (
        text.index("## Глава первая")
        < text.index("Вступление таверны.")
        < text.index("| Имя | Роль |")
        < text.index("После таблицы.")
    )
    assert "|---|---|" in text
    # Перенос строки в ячейке схлопнут в пробел.
    assert "| Бром | Трактирщик с секретом |" in text
    assert "- Пункт списка" in text
    # Пустые параграфы отброшены: не более одного пустого разделителя подряд.
    assert "\n\n\n" not in text


def test_docx_table_cell_pipe_escaped(tmp_path: Path) -> None:
    """Вертикальная черта в ячейке экранируется, чтобы не ломать Markdown-таблицу."""
    doc = Document()
    table: Table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "a|b"
    table.cell(0, 1).text = "c"
    path = tmp_path / "pipe.docx"
    doc.save(str(path))

    assert "| a\\|b | c |" in extract_text(path).text


def test_docx_russian_heading_style(tmp_path: Path) -> None:
    """Стиль «Заголовок N» (русская локаль Word) тоже даёт решётки."""
    doc = Document()
    style = doc.styles.add_style("Заголовок 3", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("Тайник", style=style)
    path = tmp_path / "ru_heading.docx"
    doc.save(str(path))

    assert extract_text(path).text == "### Тайник"


def test_docx_heading_level_clamped_to_six(tmp_path: Path) -> None:
    """Heading 9 ограничивается шестью решётками (предел Markdown)."""
    doc = Document()
    doc.add_paragraph("Глубокий раздел", style="Heading 9")
    path = tmp_path / "deep.docx"
    doc.save(str(path))

    assert extract_text(path).text == "###### Глубокий раздел"


def test_docx_numbered_list_style(tmp_path: Path) -> None:
    doc = Document()
    doc.add_paragraph("Первый шаг", style="List Number")
    path = tmp_path / "numbered.docx"
    doc.save(str(path))

    assert extract_text(path).text == "- Первый шаг"


def test_docx_numpr_xml_is_list(tmp_path: Path) -> None:
    """Нумерация прямо в XML параграфа (w:numPr) распознаётся без стиля «List …»."""
    doc = Document()
    paragraph = doc.add_paragraph("Нумерованный пункт")
    ppr = OxmlElement("w:pPr")
    numpr = OxmlElement("w:numPr")
    numpr.append(OxmlElement("w:ilvl", {qn("w:val"): "0"}))
    numpr.append(OxmlElement("w:numId", {qn("w:val"): "1"}))
    ppr.append(numpr)
    paragraph._p.insert(0, ppr)
    path = tmp_path / "numpr.docx"
    doc.save(str(path))

    assert extract_text(path).text == "- Нумерованный пункт"


# --- PDF ---


def test_pdf_pages_joined(tmp_path: Path, make_pdf: Callable[[Path, Sequence[str]], None]) -> None:
    path = tmp_path / "module.pdf"
    make_pdf(path, ["Scenario page one", "Scenario page two"])

    extracted = extract_text(path)

    assert extracted.source_format == "pdf"
    assert "Scenario page one" in extracted.text
    assert "Scenario page two" in extracted.text
    assert "\n\n" in extracted.text
    assert extracted.text.index("Scenario page one") < extracted.text.index("Scenario page two")


def test_pdf_empty_text(tmp_path: Path, make_pdf: Callable[[Path, Sequence[str]], None]) -> None:
    path = tmp_path / "empty.pdf"
    make_pdf(path, [""])

    with pytest.raises(ValueError, match="empty_document"):
        extract_text(path)


def _encrypt(src: Path, dst: Path, *, user_password: str) -> None:
    writer = PdfWriter()
    writer.append(PdfReader(src))
    writer.encrypt(user_password=user_password, owner_password="owner-secret")
    writer.write(dst)


def test_pdf_encrypted_rejected(
    tmp_path: Path, make_pdf: Callable[[Path, Sequence[str]], None]
) -> None:
    """Пароль не подошёл (пустой пароль проверен) → encrypted_pdf."""
    src = tmp_path / "plain.pdf"
    make_pdf(src, ["Secret scenario"])
    encrypted = tmp_path / "encrypted.pdf"
    _encrypt(src, encrypted, user_password="secret")

    with pytest.raises(ValueError, match="encrypted_pdf"):
        extract_text(encrypted)


def test_pdf_encrypted_empty_password_ok(
    tmp_path: Path, make_pdf: Callable[[Path, Sequence[str]], None]
) -> None:
    """PDF с пустым пользовательским паролем открывается через decrypt("")."""
    src = tmp_path / "plain.pdf"
    make_pdf(src, ["Open scenario"])
    encrypted = tmp_path / "open.pdf"
    _encrypt(src, encrypted, user_password="")

    extracted = extract_text(encrypted)

    assert extracted.source_format == "pdf"
    assert "Open scenario" in extracted.text


# --- Ошибки ---


def test_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "module.rtf"
    path.write_text("{\\rtf1 текст}", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported_format") as error:
        extract_text(path)

    assert ".docx" in str(error.value)
    assert ".pdf" in str(error.value)


def test_empty_markdown(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("  \n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty_document"):
        extract_text(path)


@pytest.mark.parametrize("suffix", [".docx", ".pdf"])
def test_broken_document(tmp_path: Path, suffix: str) -> None:
    """Мусорные байты под расширением docx/pdf → invalid_document, не traceback парсера."""
    path = tmp_path / f"broken{suffix}"
    path.write_bytes(b"garbage bytes, definitely not a document\x00\x01\x02")

    with pytest.raises(ValueError, match="invalid_document"):
        extract_text(path)


def test_file_too_large_before_parsing(tmp_path: Path) -> None:
    """Pre-check по stat: битый DOCX больше лимита даёт file_too_large, а не invalid_document."""
    path = tmp_path / "big.docx"
    with path.open("wb") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)

    with pytest.raises(ValueError, match="file_too_large"):
        extract_text(path)


def test_file_too_large_before_format_check(tmp_path: Path) -> None:
    """Размер проверяется даже раньше расширения: неподдерживаемый, но огромный файл."""
    path = tmp_path / "big.xyz"
    with path.open("wb") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)

    with pytest.raises(ValueError, match="file_too_large"):
        extract_text(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "no_such_file.md")
