"""Безопасное чтение образовательных программ DOCX и legacy DOC."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from docx import Document


class LegacyDocUnsupportedError(RuntimeError):
    """Legacy DOC нельзя безопасно прочитать без LibreOffice."""


@dataclass(frozen=True)
class ProgramContentItem:
    number: str | None
    title: str
    content: str
    parent_section: str | None = None


@dataclass(frozen=True)
class ProgramData:
    title: str | None
    duration: str | None
    student_age: str | None
    goal: str | None
    tasks: tuple[str, ...]
    lesson_forms: tuple[str, ...]
    teaching_methods: tuple[str, ...]
    expected_results: tuple[str, ...]
    content_items: tuple[ProgramContentItem, ...]


def find_libreoffice() -> Path | None:
    """Найти LibreOffice без изменения системной конфигурации."""
    executable = shutil.which("soffice") or shutil.which("soffice.exe")
    candidates = [
        executable,
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def convert_legacy_doc(data: bytes, soffice: Path | None = None) -> bytes:
    """Конвертировать временную копию DOC в DOCX и удалить все временные файлы."""
    converter = soffice or find_libreoffice()
    if converter is None:
        raise LegacyDocUnsupportedError(
            "Формат legacy .DOC пока не поддерживается: LibreOffice не найден. "
            "Исходный файл не изменён."
        )
    with tempfile.TemporaryDirectory(prefix="calendar_pedagoga_") as temp_name:
        temp_dir = Path(temp_name)
        source = temp_dir / "program.doc"
        source.write_bytes(data)
        profile = temp_dir / "lo_profile"
        output = temp_dir / "program.docx"
        command = [
            str(converter),
            "--headless",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "docx",
            "--outdir",
            str(temp_dir),
            str(source),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            message = (completed.stderr or completed.stdout).strip()
            raise LegacyDocUnsupportedError(
                "LibreOffice не смог преобразовать legacy .DOC в DOCX"
                + (f": {message}" if message else ".")
            )
        return output.read_bytes()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(1))
    return None


def _number_title(value: str) -> tuple[str | None, str]:
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$", _clean(value))
    return (match.group(1), _clean(match.group(2))) if match else (None, _clean(value))


def _collect_after(
    paragraphs: list[str],
    heading_pattern: str,
    stop_pattern: str,
) -> tuple[str, ...]:
    result: list[str] = []
    active = False
    for paragraph in paragraphs:
        if re.search(heading_pattern, paragraph, re.IGNORECASE):
            active = True
            remainder = re.sub(
                rf"^.*?{heading_pattern}\s*[:.-]?\s*",
                "",
                paragraph,
                flags=re.IGNORECASE,
            )
            if _clean(remainder):
                result.append(_clean(remainder))
            continue
        if active and re.search(stop_pattern, paragraph, re.IGNORECASE):
            break
        if active and _clean(paragraph):
            result.append(_clean(re.sub(r"^[•\-–—]\s*", "", paragraph)))
    return tuple(result)


def _is_bold_heading(paragraph) -> bool:
    runs = [run for run in paragraph.runs if _clean(run.text)]
    return bool(runs) and all(run.bold is True for run in runs)


def _content_items(document, study_year: int) -> tuple[ProgramContentItem, ...]:
    paragraphs = document.paragraphs
    start = next(
        (
            index for index, paragraph in enumerate(paragraphs)
            if re.search(rf"содержание\s+программы\s+{study_year}-го\s+года\s+обучения", _clean(paragraph.text), re.I)
        ),
        None,
    )
    if start is None:
        return ()
    items: list[ProgramContentItem] = []
    current_number: str | None = None
    current_title: str | None = None
    current_parent: str | None = None
    content: list[str] = []

    def flush() -> None:
        if current_title:
            items.append(
                ProgramContentItem(
                    current_number,
                    current_title,
                    "\n".join(content).strip(),
                    current_parent,
                )
            )

    for paragraph in paragraphs[start + 1 :]:
        text = _clean(paragraph.text)
        if not text:
            continue
        if re.search(r"по окончани[юя].*года обучения", text, re.I):
            break
        number, title = _number_title(text)
        if _is_bold_heading(paragraph):
            flush()
            current_number, current_title, content = number, title, []
            if number and "." not in number:
                current_parent = title
            elif number is None and paragraph.alignment == 1:
                current_parent = title
            continue
        if current_title is None:
            continue
        content.append(text)
    flush()
    return tuple(items)


def parse_program_docx(data: bytes, study_year: int = 2) -> ProgramData:
    """Извлечь только явно присутствующие формулировки из DOCX."""
    document = Document(BytesIO(data))
    paragraphs = [_clean(p.text) for p in document.paragraphs if _clean(p.text)]
    text = "\n".join(paragraphs)
    stop = (
        r"цель|задач|формы?\s+организац|методы?\s+обучен|"
        r"ожидаем|планируем|содержание"
    )
    tasks = _collect_after(paragraphs, r"задачи(?: программы)?", r"хочу заметить|срок реализации|" + stop)
    forms = tuple(_clean(match.group(1)) for paragraph in paragraphs if (match := re.match(r"формы?\s+организации\s+(?:образовательного\s+процесса|занятий)\s*[–—:-]\s*(.+)", paragraph, re.I)))
    methods = tuple(_clean(match.group(1)) for paragraph in paragraphs if (match := re.match(r"методы?\s+обучения\s*[:.-]\s*(.+)", paragraph, re.I)))
    results = tuple(paragraph for paragraph in paragraphs if re.match(r"за период освоения.*ожидается", paragraph, re.I))
    if not results:
        results = _collect_after(paragraphs, r"(?:ожидаемые|планируемые)\s+результаты", stop)
    return ProgramData(
        title=_first_match(
            text,
            (
                r"(?:программа|направленность)\s*[«\"]([^»\"]+)[»\"]",
                r"название программы\s*[:.-]\s*([^\n]+)",
                r"(?m)^[«\"]([^»\"]+)[»\"]$",
            ),
        ),
        duration=_first_match(
            text,
            (
                r"срок реализации(?: программы)?\s*[:.-]\s*([^\n]+)",
                r"программа рассчитана на\s*([^\n]+)",
            ),
        ),
        student_age=_first_match(
            text,
            (
                r"возраст (?:обучающихся|учащихся|детей)\s*[:.-]\s*([^\n]+)",
            ),
        ),
        goal=_first_match(
            text,
            (r"цель(?: программы)?\s*[:.-]\s*([^\n]+)",),
        ),
        tasks=tasks,
        lesson_forms=forms,
        teaching_methods=methods,
        expected_results=results,
        content_items=_content_items(document, study_year),
    )


def parse_program(data: bytes, filename: str) -> ProgramData:
    """Разобрать загруженную программу с безопасной обработкой расширения."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".doc":
        return parse_program_docx(convert_legacy_doc(data))
    if suffix == ".docx":
        return parse_program_docx(data)
    raise ValueError("Поддерживаются только файлы DOC и DOCX.")
