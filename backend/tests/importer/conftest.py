"""Фикстуры тестов импорта (подэтап 2.5): конфиг LLM, бестиарий, черновик, PDF-билдер.

Всё офлайн: конфиг читается из файла, модель не вызывается, база не нужна.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ai_dnd.ai.config import LLMConfig, load_llm_config
from ai_dnd.ai.schemas import LLMResponse, Usage
from ai_dnd.config import Settings
from ai_dnd.rules.ruleset import Ruleset
from ai_dnd.scenarios.schemas import Bestiary
from ai_dnd.scenarios.service import load_bestiary


@pytest.fixture(scope="session")
def llm_config() -> LLMConfig:
    """Конфиг LLM из backend/config/llm.yaml, без .env разработчика."""
    return load_llm_config(Settings(_env_file=None).llm_config)


@pytest.fixture(scope="session")
def srd_bestiary(content_dir: Path, srd5_ruleset: Ruleset) -> Bestiary:
    """Библиотека статблоков SRD (srd:commoner, srd:skeleton, ...)."""
    return load_bestiary(content_dir / "bestiary" / "srd.yaml", srd5_ruleset)


@pytest.fixture
def draft_data() -> dict[str, Any]:
    """Валидный черновик импорта. Тесты портят его по одному месту."""
    return {
        "scenario": {
            "scenario": {
                "class_pack": "fantasy-core",
                "title": "Тестовый импорт",
                "language": "ru",
                "ruleset": "dnd5e-srd@1",
                "sheet_template": "srd5-basic@1",
                "start_scene": "square",
                "synopsis": "Герои исследуют площадь и спускаются в усыпальницу.",
            },
            "scenes": [
                {
                    "id": "square",
                    "title": "Площадь",
                    "read_aloud": "Тихая площадь.",
                    "npcs": ["elder"],
                    "exits": ["crypt"],
                    "checks": [
                        {
                            "id": "look",
                            "skill": "perception",
                            "dc": 12,
                            "on_success": {"reveal": ["clue_marks"]},
                        }
                    ],
                },
                {"id": "crypt", "title": "Усыпальница", "exits": ["square"]},
            ],
            "npcs": [{"id": "elder", "name": "Староста", "statblock": "srd:commoner"}],
            "clues": [{"id": "clue_marks", "text": "Имена стёрты ножом."}],
        },
        "notes": [
            {"kind": "assumption", "path": "scenes.square.checks.look", "text": "DC взят 12."}
        ],
    }


def _make_response(cost: Decimal) -> LLMResponse:
    """Ответ модели с заданной стоимостью — для отчёта без вызова шлюза."""
    return LLMResponse(
        text="{}",
        finish_reason="stop",
        usage=Usage(input_tokens=1000, output_tokens=200),
        cost=cost,
        task="import",
        provider="deepseek",
        model="scripted",
        latency_ms=1,
    )


@pytest.fixture
def make_response() -> Callable[[Decimal], LLMResponse]:
    return _make_response


def _write_pdf(path: Path, texts: Sequence[str]) -> None:
    """Минимальный валидный PDF: по странице на текст, шрифт Helvetica (ASCII-текст)."""
    objects: list[bytes] = []
    n_pages = len(texts)
    page_ids = [3 + 2 * i for i in range(n_pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    font_id = 3 + 2 * n_pages
    for i, text in enumerate(texts):
        content_id = 4 + 2 * i
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


@pytest.fixture
def make_pdf() -> Callable[[Path, Sequence[str]], None]:
    return _write_pdf
