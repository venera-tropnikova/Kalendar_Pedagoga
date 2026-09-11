"""Выбор источника УТП: отдельный файл или таблица внутри программы."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from calendar_pedagoga.matching import normalize_title
from calendar_pedagoga.parsing import (
    Hours,
    Section,
    Topic,
    UtpMetadata,
    UtpParseResult,
    UtpYearSelectionError,
    collect_utp_table_candidates,
    parse_utp,
)
from calendar_pedagoga.program_parsing import (
    ProgramData,
    convert_legacy_doc,
    infer_study_year_number,
)
from calendar_pedagoga.upload_validation import ValidatedUpload


AUTO_WORKLOAD_WARNING = "Недельная нагрузка определена автоматически: 36 недель × 2 часа."
RECONCILE_PASS = "PASS"
RECONCILE_NOTICE = "NOTICE"
RECONCILE_LEAD = (
    "Отдельный УТП выбран источником плана, но он расходится с "
    "учебно-тематическим планом того же года в программе:"
)
UTP_PROGRAM_MISMATCH_MESSAGE = (
    "УТП, вероятно, относится к другой программе. Проверьте загруженный файл."
)
UTP_PROGRAM_UNCERTAIN_NOTICE = (
    "Не удалось уверенно подтвердить соответствие УТП программе. Проверьте файл."
)
_IDENTITY_STOPWORDS = frozenset(
    {
        "дополнительная",
        "общеобразовательная",
        "общеразвивающая",
        "образовательная",
        "программа",
        "утп",
        "учебный",
        "учебно",
        "тематический",
        "план",
        "год",
        "обучения",
        "час",
        "часа",
        "часов",
        "doc",
        "docx",
    }
)
_CONTENT_STOPWORDS = _IDENTITY_STOPWORDS | frozenset(
    {"раздел", "тема", "основы", "занятие", "занятия", "практика", "теория"}
)


class UtpResolutionError(ValueError):
    """Не удалось получить УТП из загруженных документов."""


def _program_docx_bytes(program: ValidatedUpload) -> bytes:
    if program.filename.lower().endswith(".doc"):
        return convert_legacy_doc(program.content)
    return program.content


def _with_metadata(
    result: UtpParseResult,
    metadata: UtpMetadata,
    extra_warnings: tuple[str, ...] = (),
) -> UtpParseResult:
    return UtpParseResult(
        metadata=metadata,
        sections=result.sections,
        topics=result.topics,
        table_totals=result.table_totals,
        warnings=tuple(dict.fromkeys((*result.warnings, *extra_warnings))),
    )


def apply_workload_from_document(result: UtpParseResult) -> UtpParseResult:
    """Дополнить недели/часы в неделю только при надёжном определении."""

    metadata = result.metadata
    yearly = metadata.hours_per_year
    if yearly is None and result.table_totals is not None:
        yearly = result.table_totals.total
    weeks = metadata.study_weeks
    weekly = metadata.hours_per_week
    extra: list[str] = []

    if weeks and weekly:
        provenance = metadata.workload_provenance or "document"
        return _with_metadata(result, replace(metadata, workload_provenance=provenance))

    if yearly and weeks and weekly is None and weeks > 0 and yearly % weeks == 0:
        weekly = yearly // weeks
        extra.append(
            f"Недельная нагрузка определена автоматически: {weeks} недель × {weekly} часа."
        )
        return _with_metadata(
            result,
            replace(
                metadata,
                hours_per_week=weekly,
                hours_per_year=yearly,
                workload_provenance="derived",
            ),
            tuple(extra),
        )

    if yearly and weekly and weeks is None and weekly > 0 and yearly % weekly == 0:
        weeks = yearly // weekly
        extra.append(
            f"Недельная нагрузка определена автоматически: {weeks} недель × {weekly} часа."
        )
        return _with_metadata(
            result,
            replace(
                metadata,
                study_weeks=weeks,
                hours_per_year=yearly,
                workload_provenance="derived",
            ),
            tuple(extra),
        )

    if yearly == 72 and weeks in {None, 36} and weekly in {None, 2}:
        extra.append(AUTO_WORKLOAD_WARNING)
        return _with_metadata(
            result,
            replace(
                metadata,
                hours_per_year=72,
                study_weeks=36,
                hours_per_week=2,
                workload_provenance="derived_36x2",
            ),
            tuple(extra),
        )

    raise UtpResolutionError(
        "Не удалось надёжно определить число учебных недель и часов в неделю. "
        "Укажите их в УТП или загрузите отдельный учебно-тематический план."
    )


def _hours_label(hours: Hours | None) -> str:
    if hours is None:
        return "нет"
    return f"{hours.total}/{hours.theory}/{hours.practice}"


def _section_key(section: Section) -> tuple[str | None, str]:
    return (section.number, normalize_title(section.title))


def _topic_key(topic: Topic) -> tuple[str | None, str]:
    return (topic.number, normalize_title(topic.title))


def _meaningful_tokens(*values: str | None, stopwords: frozenset[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        spaced = re.sub(r"(?<=\D)(?=\d)|(?<=\d)(?=\D)", " ", value)
        for token in re.findall(r"[a-zа-яё]+", normalize_title(spaced)):
            if len(token) >= 3 and token not in stopwords:
                tokens.add(token)
    return tokens


def _coverage(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _program_utp_compatibility(
    separate: UtpParseResult,
    embedded: UtpParseResult,
    program_document: ValidatedUpload,
    separate_filename: str,
) -> str:
    """Classify identity using independent name and content signals."""

    program = program_document.parsed
    program_title = program.title if isinstance(program, ProgramData) else None
    program_identity = _meaningful_tokens(
        program_title,
        Path(program_document.filename).stem,
        stopwords=_IDENTITY_STOPWORDS,
    )
    utp_identity = _meaningful_tokens(
        separate.metadata.program_name,
        Path(separate_filename).stem,
        stopwords=_IDENTITY_STOPWORDS,
    )
    identity_known = bool(program_identity and utp_identity)
    identity_matches = identity_known and _coverage(program_identity, utp_identity) >= 0.5
    identity_conflicts = identity_known and not (program_identity & utp_identity)

    embedded_titles = tuple(
        item.title for item in (*embedded.sections, *embedded.topics)
    )
    separate_titles = tuple(
        item.title for item in (*separate.sections, *separate.topics)
    )
    embedded_content = _meaningful_tokens(
        *embedded_titles,
        stopwords=_CONTENT_STOPWORDS,
    )
    separate_content = _meaningful_tokens(
        *separate_titles,
        stopwords=_CONTENT_STOPWORDS,
    )
    content_known = bool(embedded_content and separate_content)
    content_matches = (
        content_known and _coverage(embedded_content, separate_content) >= 0.25
    )

    if identity_conflicts and content_known and not content_matches:
        return "BLOCK"
    if identity_matches or content_matches:
        return "PASS"
    return "NOTICE"


def compare_embedded_to_separate(
    embedded: UtpParseResult,
    separate: UtpParseResult,
) -> tuple[str, tuple[str, ...]]:
    """Сверить структуру и часы выбранного embedded УТП с отдельным файлом."""

    diffs: list[str] = []
    if embedded.table_totals != separate.table_totals:
        diffs.append(
            "Итоговые часы: программа "
            f"{_hours_label(embedded.table_totals)}, отдельный УТП "
            f"{_hours_label(separate.table_totals)}."
        )

    emb_sections = {_section_key(item): item for item in embedded.sections}
    sep_sections = {_section_key(item): item for item in separate.sections}
    for key in emb_sections.keys() - sep_sections.keys():
        item = emb_sections[key]
        diffs.append(
            f"Раздел {item.number or '—'} «{item.title}»: есть в программе, "
            "нет в отдельном УТП."
        )
    for key in sep_sections.keys() - emb_sections.keys():
        item = sep_sections[key]
        diffs.append(
            f"Раздел {item.number or '—'} «{item.title}»: есть в отдельном УТП, "
            "нет в программе."
        )
    for key in emb_sections.keys() & sep_sections.keys():
        left, right = emb_sections[key], sep_sections[key]
        if left.hours != right.hours:
            diffs.append(
                f"Раздел {left.number or '—'} «{left.title}»: часы программы "
                f"{_hours_label(left.hours)}, отдельного УТП "
                f"{_hours_label(right.hours)}."
            )
    if [_section_key(item) for item in embedded.sections] != [
        _section_key(item) for item in separate.sections
    ] and emb_sections.keys() == sep_sections.keys():
        diffs.append("Порядок разделов программы и отдельного УТП различается.")

    emb_topics = {_topic_key(item): item for item in embedded.topics}
    sep_topics = {_topic_key(item): item for item in separate.topics}
    for key in emb_topics.keys() - sep_topics.keys():
        item = emb_topics[key]
        diffs.append(
            f"Тема {item.number or '—'} «{item.title}»: есть в программе, "
            "нет в отдельном УТП."
        )
    for key in sep_topics.keys() - emb_topics.keys():
        item = sep_topics[key]
        diffs.append(
            f"Тема {item.number or '—'} «{item.title}»: есть в отдельном УТП, "
            "нет в программе."
        )
    for key in emb_topics.keys() & sep_topics.keys():
        left, right = emb_topics[key], sep_topics[key]
        if left.hours != right.hours:
            diffs.append(
                f"Тема {left.number or '—'} «{left.title}»: часы программы "
                f"{_hours_label(left.hours)}, отдельного УТП "
                f"{_hours_label(right.hours)}."
            )
    if [_topic_key(item) for item in embedded.topics] != [
        _topic_key(item) for item in separate.topics
    ] and emb_topics.keys() == sep_topics.keys():
        diffs.append("Порядок тем программы и отдельного УТП различается.")

    if not diffs:
        return RECONCILE_PASS, ()
    return RECONCILE_NOTICE, tuple(diffs)


def study_year_from_utp(
    result: UtpParseResult,
    filename: str | None = None,
) -> int | None:
    """Requested study_year для выбора embedded УТП.

    Приоритет: metadata отдельного УТП, затем имя его файла.
    Похожесть часов/тем и score таблиц год не выбирают.
    """

    return infer_study_year_number(result.metadata.study_year) or infer_study_year_number(
        filename
    )


def _embedded_years(program_bytes: bytes) -> tuple[int, ...]:
    years = [
        item.study_year
        for item in collect_utp_table_candidates(program_bytes)
        if item.study_year is not None
    ]
    return tuple(dict.fromkeys(years))


def _reconcile_separate(
    separate: UtpParseResult,
    embedded: UtpParseResult,
    separate_year: int | None,
    compatibility_notice: tuple[str, ...] = (),
) -> UtpParseResult:
    embedded_year = infer_study_year_number(embedded.metadata.study_year)
    if (
        separate_year is not None
        and embedded_year is not None
        and separate_year != embedded_year
    ):
        raise UtpResolutionError(
            f"Год обучения программы ({embedded_year}) и отдельного УТП "
            f"({separate_year}) противоречат друг другу."
        )
    status, diffs = compare_embedded_to_separate(embedded, separate)
    extra: tuple[str, ...] = ()
    if status == RECONCILE_NOTICE:
        extra = (RECONCILE_LEAD, *diffs)
    return apply_workload_from_document(
        _with_metadata(separate, separate.metadata, (*compatibility_notice, *extra))
    )


def resolve_utp(
    optional_utp_upload: ValidatedUpload | None,
    program_document: ValidatedUpload,
) -> UtpParseResult:
    """Вернуть УТП: отдельный файл — источник плана; embedded — сверка того же года.

    Requested year: separate.metadata.study_year, иначе имя separate-файла.
    Этот год выбирает embedded-таблицу. Часы/темы того же года дают NOTICE,
    а не BLOCK. Явный чужой год программы — BLOCK, без fallback на «похожую»
    таблицу другого года.
    """

    program_bytes = _program_docx_bytes(program_document)
    if optional_utp_upload is not None:
        parsed = optional_utp_upload.parsed
        if not isinstance(parsed, UtpParseResult):
            raise UtpResolutionError(
                "Загруженный файл УТП не содержит учебно-тематический план."
            )
        separate_year = study_year_from_utp(parsed, optional_utp_upload.filename)
        try:
            embedded = parse_utp(program_bytes, study_year=separate_year)
        except UtpYearSelectionError as error:
            found_years = _embedded_years(program_bytes)
            if (
                separate_year is not None
                and len(found_years) == 1
                and separate_year not in found_years
            ):
                raise UtpResolutionError(
                    f"Год обучения программы ({found_years[0]}) и отдельного УТП "
                    f"({separate_year}) противоречат друг другу."
                ) from error
            raise UtpResolutionError(str(error)) from error
        except Exception:
            return apply_workload_from_document(parsed)
        compatibility = _program_utp_compatibility(
            parsed,
            embedded,
            program_document,
            optional_utp_upload.filename,
        )
        if compatibility == "BLOCK":
            raise UtpResolutionError(UTP_PROGRAM_MISMATCH_MESSAGE)
        notice = (
            (UTP_PROGRAM_UNCERTAIN_NOTICE,)
            if compatibility == "NOTICE"
            else ()
        )
        return _reconcile_separate(parsed, embedded, separate_year, notice)

    try:
        embedded = parse_utp(program_bytes)
    except UtpYearSelectionError as error:
        raise UtpResolutionError(str(error)) from error
    except Exception as error:
        raise UtpResolutionError(
            "В программе не найден учебно-тематический план. "
            "Загрузите УТП отдельным файлом."
        ) from error
    if not embedded.topics or not embedded.sections:
        raise UtpResolutionError(
            "В программе не найден учебно-тематический план. "
            "Загрузите УТП отдельным файлом."
        )
    return apply_workload_from_document(embedded)
