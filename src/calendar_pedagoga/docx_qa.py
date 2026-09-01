"""Структурная и визуальная проверка сгенерированного календарного DOCX."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from docx import Document

from calendar_pedagoga.program_parsing import find_libreoffice


class QASeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class QAIssue:
    severity: QASeverity
    message: str


@dataclass(frozen=True)
class VisualPageReport:
    page_number: int
    path: Path
    width: int
    height: int
    size_bytes: int
    ink_ratio: float


_REQUIRED_HEADER_MARKERS = (
    "месяц",
    "неделя",
    "теоретические",
    "практические",
    "тип занятия",
    "планируемый результат",
    "вид контроля",
)

_MIN_PAGE_FILE_BYTES = 8_000
_MIN_INK_RATIO = 0.01
_MIN_PAGE_DIMENSION = 400


def validate_calendar_docx(
    content: bytes,
    *,
    expected_weeks: int,
) -> tuple[QAIssue, ...]:
    """Проверить DOCX после генерации: структура, таблица и постраничная целостность."""

    issues: list[QAIssue] = []
    try:
        document = Document(BytesIO(content))
    except Exception as error:
        return (QAIssue(QASeverity.ERROR, f"DOCX не читается: {error}"),)

    if not document.paragraphs or not document.paragraphs[0].text.strip():
        issues.append(QAIssue(QASeverity.ERROR, "Отсутствует заголовок документа."))
    elif "календар" not in document.paragraphs[0].text.casefold():
        issues.append(QAIssue(QASeverity.ERROR, "Первый абзац не является заголовком календаря."))

    if len(document.paragraphs) < 3:
        issues.append(QAIssue(QASeverity.ERROR, "Недостаточно служебных абзацев шапки."))

    if not document.tables:
        issues.append(QAIssue(QASeverity.ERROR, "В документе нет таблицы календаря."))
        return tuple(issues)

    if len(document.tables) != 1:
        issues.append(QAIssue(QASeverity.WARNING, "Ожидалась одна таблица календаря."))

    table = document.tables[0]
    if len(table.rows) < 2:
        issues.append(QAIssue(QASeverity.ERROR, "Таблица не содержит строк заголовка."))
        return tuple(issues)

    header_text = " ".join(
        cell.text for row in table.rows[:2] for cell in row.cells
    ).casefold()
    missing = [marker for marker in _REQUIRED_HEADER_MARKERS if marker not in header_text]
    if missing:
        issues.append(
            QAIssue(
                QASeverity.ERROR,
                "В заголовке таблицы отсутствуют обязательные колонки: "
                + ", ".join(missing),
            )
        )

    data_rows = table.rows[2:]
    if len(data_rows) != expected_weeks:
        issues.append(
            QAIssue(
                QASeverity.ERROR,
                f"Ожидалось {expected_weeks} строк данных, найдено {len(data_rows)}.",
            )
        )

    column_count = len(table.columns)
    if column_count < 8:
        issues.append(
            QAIssue(
                QASeverity.ERROR,
                f"Недостаточно колонок в таблице: {column_count}.",
            )
        )

    for section in document.sections:
        if section.page_width <= 0 or section.page_height <= 0:
            issues.append(
                QAIssue(QASeverity.ERROR, "Некорректные параметры страницы документа.")
            )
            break
        if section.left_margin <= 0 or section.right_margin <= 0:
            issues.append(
                QAIssue(
                    QASeverity.WARNING,
                    "Нетипичные поля страницы; проверьте визуальное отображение.",
                )
            )

    if document.tables:
        table = document.tables[0]
        tbl_pr = table._tbl.tblPr
        tbl_width = getattr(tbl_pr, "tblW", None) if tbl_pr is not None else None
        if tbl_pr is not None and tbl_width is None:
            issues.append(
                QAIssue(
                    QASeverity.WARNING,
                    "Ширина таблицы не задана явно; возможны переносы на новые страницы.",
                )
            )
        for index, row in enumerate(table.rows[2:], start=1):
            if not any(cell.text.strip() for cell in row.cells[:5]):
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Строка {index + 2}: полностью пустая строка календаря.",
                    )
                )

    week_numbers: list[int] = []
    for index, row in enumerate(data_rows, start=1):
        cells = row.cells
        if len(cells) < 8:
            issues.append(
                QAIssue(
                    QASeverity.ERROR,
                    f"Строка {index + 2}: неполный набор ячеек ({len(cells)}).",
                )
            )
            continue

        month = cells[0].text.strip()
        week_cell = cells[1].text.strip()
        if not month:
            issues.append(
                QAIssue(QASeverity.ERROR, f"Строка {index + 2}: пустой месяц.")
            )
        if not week_cell:
            issues.append(
                QAIssue(QASeverity.ERROR, f"Строка {index + 2}: пустая неделя/дата.")
            )
        else:
            match = re.search(r"(\d+)", week_cell.splitlines()[0])
            if match:
                week_numbers.append(int(match.group(1)))
            else:
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Строка {index + 2}: не распознан номер недели.",
                    )
                )

        for cell in cells:
            if cell.text is None:
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Строка {index + 2}: недоступная ячейка таблицы.",
                    )
                )

    if week_numbers and week_numbers != list(range(1, len(week_numbers) + 1)):
        issues.append(
            QAIssue(
                QASeverity.ERROR,
                "Нумерация недель в таблице не непрерывна.",
            )
        )

    return tuple(issues)


def has_blocking_qa_issues(issues: tuple[QAIssue, ...]) -> bool:
    return any(issue.severity is QASeverity.ERROR for issue in issues)


def _pdf_page_count(pdf_path: Path) -> int:
    data = pdf_path.read_bytes()
    counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", data)]
    return max(counts) if counts else 0


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        signature = handle.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"Not a PNG: {path}")
        handle.read(4)
        chunk = handle.read(4)
        if chunk != b"IHDR":
            raise ValueError(f"PNG IHDR missing: {path}")
        width, height = struct.unpack(">II", handle.read(8))
        return width, height


def _png_ink_ratio(path: Path) -> float:
    """Доля не-белых пикселей (эвристика читаемости)."""

    try:
        from PIL import Image
    except ImportError:
        return _png_ink_ratio_fallback(path)

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        step = max(1, (width * height) // 20_000)
        dark = 0
        seen = 0
        for index in range(0, width * height, step):
            x = index % width
            y = index // width
            red, green, blue = rgb.getpixel((x, y))
            seen += 1
            if red < 245 or green < 245 or blue < 245:
                dark += 1
        return dark / seen if seen else 0.0


def _png_ink_ratio_fallback(path: Path) -> float:
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return 0.0
    return 0.05 if path.stat().st_size >= _MIN_PAGE_FILE_BYTES else 0.0


def render_docx_pages(content: bytes, output_dir: Path) -> tuple[Path, ...]:
    """Отрендерить все страницы DOCX в PNG через LibreOffice (DOCX→PDF→PNG)."""

    soffice = find_libreoffice()
    if soffice is None:
        raise RuntimeError("LibreOffice не найден для visual QA.")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="calendar_pedagoga_qa_") as temp_name:
        temp_path = Path(temp_name)
        docx_path = temp_path / "calendar.docx"
        docx_path.write_bytes(content)
        pdf_dir = temp_path / "pdf_full"
        pdf_dir.mkdir()

        result = subprocess.run(
            [
                str(soffice),
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice PDF convert failed: {result.stderr or result.stdout}"
            )

        pdfs = sorted(pdf_dir.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("LibreOffice не создал PDF для visual QA.")
        page_count = _pdf_page_count(pdfs[0])
        if page_count <= 0:
            raise RuntimeError("Не удалось определить число страниц PDF.")

        copied: list[Path] = []
        for page_number in range(1, page_count + 1):
            page_pdf_dir = temp_path / f"pdf_page_{page_number}"
            page_png_dir = temp_path / f"png_page_{page_number}"
            page_pdf_dir.mkdir()
            page_png_dir.mkdir()
            page_filter = (
                "pdf:writer_pdf_Export:"
                f'{{"PageRange":{{"type":"string","value":"{page_number}"}}}}'
            )
            page_pdf = subprocess.run(
                [
                    str(soffice),
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    page_filter,
                    "--outdir",
                    str(page_pdf_dir),
                    str(docx_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if page_pdf.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice page PDF export failed for page {page_number}."
                )
            single_pdfs = sorted(page_pdf_dir.glob("*.pdf"))
            if not single_pdfs:
                raise RuntimeError(f"LibreOffice не создал PDF для страницы {page_number}.")

            page_png = subprocess.run(
                [
                    str(soffice),
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "png",
                    "--outdir",
                    str(page_png_dir),
                    str(single_pdfs[0]),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if page_png.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice PNG convert failed for page {page_number}."
                )
            pngs = sorted(page_png_dir.glob("*.png"))
            if not pngs:
                raise RuntimeError(f"LibreOffice не создал PNG для страницы {page_number}.")
            target = output_dir / f"page_{page_number:02d}.png"
            shutil.copy2(pngs[0], target)
            copied.append(target)
        return tuple(copied)


def analyze_visual_pages(png_paths: tuple[Path, ...]) -> tuple[VisualPageReport, ...]:
    reports: list[VisualPageReport] = []
    for index, path in enumerate(png_paths, start=1):
        width, height = _png_dimensions(path)
        reports.append(
            VisualPageReport(
                page_number=index,
                path=path,
                width=width,
                height=height,
                size_bytes=path.stat().st_size,
                ink_ratio=_png_ink_ratio(path),
            )
        )
    return tuple(reports)


def validate_calendar_docx_visual(content: bytes) -> tuple[QAIssue, ...]:
    """Visual QA: отрендерить все страницы и проверить читаемость/целостность."""

    issues: list[QAIssue] = []
    soffice = find_libreoffice()
    if soffice is None:
        return (
            QAIssue(
                QASeverity.ERROR,
                "LibreOffice недоступен; visual QA всех страниц невозможен.",
            ),
        )

    with tempfile.TemporaryDirectory(prefix="calendar_pedagoga_visual_") as temp_name:
        try:
            png_paths = render_docx_pages(content, Path(temp_name))
            reports = analyze_visual_pages(png_paths)
        except RuntimeError as error:
            return (QAIssue(QASeverity.ERROR, str(error)),)

        if not reports:
            return (QAIssue(QASeverity.ERROR, "Не удалось отрендерить страницы DOCX."),)

        for report in reports:
            if report.size_bytes < _MIN_PAGE_FILE_BYTES:
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Страница {report.page_number}: подозрение на пустой/обрезанный рендер.",
                    )
                )
            if report.width < _MIN_PAGE_DIMENSION or report.height < _MIN_PAGE_DIMENSION:
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Страница {report.page_number}: некорректный размер изображения.",
                    )
                )
            if report.ink_ratio < _MIN_INK_RATIO:
                issues.append(
                    QAIssue(
                        QASeverity.ERROR,
                        f"Страница {report.page_number}: недостаточно видимого содержания.",
                    )
                )

        first = reports[0]
        if first.ink_ratio < 0.02:
            issues.append(
                QAIssue(
                    QASeverity.ERROR,
                    "Страница 1: шапка/таблица не видны на рендере.",
                )
            )
        last = reports[-1]
        if last.ink_ratio < _MIN_INK_RATIO:
            issues.append(
                QAIssue(
                    QASeverity.ERROR,
                    "Последняя страница: таблица не читается или обрезана.",
                )
            )

    return tuple(issues)
