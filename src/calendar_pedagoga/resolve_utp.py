"""Выбор источника УТП: отдельный файл или таблица внутри программы."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from calendar_pedagoga.matching import normalize_title
from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlan,
    ConfirmedStudyPlanError,
    confirmed_plan_from_external_utp,
)
from calendar_pedagoga.parsing import (
    HourValue,
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
SEPARATE_WEEKLY_REQUIRED = (
    "Во внешнем УТП не указана постоянная недельная нагрузка. "
    "Укажите число часов в неделю явно — система не подставляет 2 ч/нед "
    "и не выводит нагрузку из годового итога."
)
SEPARATE_YEARLY_MISMATCH = (
    "Годовой итог внешнего УТП не согласуется с числом недель и часов в неделю. "
    "Уточните нагрузку во внешнем УТП."
)
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
MISSING_STUDY_WEEKS_MESSAGE = "Укажите количество учебных недель."
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
    """Дополнить недели/часы в неделю для embedded УТП при надёжном определении."""

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


def apply_workload_from_separate(result: UtpParseResult) -> UtpParseResult:
    """Нагрузка внешнего УТП: явные недели и ч/нед, без вычисления из итога.

    Темы и table_totals не меняются. Любая неоднозначность — BLOCK.
    """

    metadata = result.metadata
    yearly = metadata.hours_per_year
    if yearly is None and result.table_totals is not None:
        yearly = result.table_totals.total
    weekly = metadata.hours_per_week
    weeks = metadata.study_weeks

    if weekly is None:
        raise UtpResolutionError(SEPARATE_WEEKLY_REQUIRED)

    if weeks is None:
        raise UtpResolutionError(MISSING_STUDY_WEEKS_MESSAGE)
    if weeks <= 0:
        raise UtpResolutionError(MISSING_STUDY_WEEKS_MESSAGE)
    provenance = metadata.workload_provenance or "document"

    if yearly is not None and weeks * weekly != yearly:
        raise UtpResolutionError(SEPARATE_YEARLY_MISMATCH)

    return _with_metadata(
        result,
        replace(
            metadata,
            hours_per_week=weekly,
            study_weeks=weeks,
            hours_per_year=yearly,
            workload_provenance=provenance,
        ),
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


def embedded_study_years(program_document: ValidatedUpload) -> tuple[int, ...]:
    """Return only study years that are explicitly present in embedded UTPs."""

    return _embedded_years(_program_docx_bytes(program_document))


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
    if (
        separate_year is not None
        and infer_study_year_number(separate.metadata.study_year) is None
    ):
        separate = _with_metadata(
            separate,
            replace(
                separate.metadata,
                study_year=f"{separate_year} год обучения",
            ),
        )
    return apply_workload_from_separate(
        _with_metadata(separate, separate.metadata, (*compatibility_notice, *extra))
    )


def apply_user_study_weeks(
    result: UtpParseResult,
    study_weeks: int | None,
) -> UtpParseResult:
    """Fill only missing embedded-UTP weeks with an explicit user value."""

    metadata = result.metadata
    if metadata.study_weeks is not None or study_weeks is None:
        return result
    if study_weeks <= 0:
        raise UtpResolutionError(MISSING_STUDY_WEEKS_MESSAGE)
    yearly = metadata.hours_per_year
    if yearly is None and result.table_totals is not None:
        yearly = result.table_totals.total
    weekly = metadata.hours_per_week
    if yearly is not None and weekly is not None and study_weeks * weekly != yearly:
        raise UtpResolutionError(
            "Количество учебных недель не согласуется с нагрузкой программы: "
            f"{study_weeks} × {weekly} = {study_weeks * weekly} ч., "
            f"в программе указано {yearly} ч."
        )
    return _with_metadata(
        result,
        replace(
            metadata,
            study_weeks=study_weeks,
            workload_provenance="user_study_weeks",
        ),
    )


def resolve_utp(
    optional_utp_upload: ValidatedUpload | None,
    program_document: ValidatedUpload,
    *,
    program_study_year: int | None = None,
    program_study_weeks: int | None = None,
    study_weeks: int | None = None,
    hours_per_week: HourValue | None = None,
) -> ConfirmedStudyPlan:
    """Resolve the production plan only from a confirmed external UTP.

    PROGRAM remains a semantic/matching source and never supplies production
    topics or workload. Missing external workload values may only be supplied
    explicitly by the user; they are never derived here.
    """

    del program_document, program_study_weeks
    if optional_utp_upload is not None:
        parsed = optional_utp_upload.parsed
        if not isinstance(parsed, UtpParseResult):
            raise UtpResolutionError(
                "Загруженный файл УТП не содержит учебно-тематический план."
            )
        separate_year = study_year_from_utp(parsed, optional_utp_upload.filename)
        if (
            separate_year is not None
            and program_study_year is not None
            and separate_year != program_study_year
        ):
            raise UtpResolutionError(
                f"Год обучения программы ({program_study_year}) и отдельного УТП "
                f"({separate_year}) противоречат друг другу."
            )
        try:
            return confirmed_plan_from_external_utp(
                parsed,
                study_year=program_study_year or separate_year,
                source_name=optional_utp_upload.filename,
                study_weeks=study_weeks,
                hours_per_week=hours_per_week,
            )
        except ConfirmedStudyPlanError as error:
            raise UtpResolutionError(str(error)) from error

    raise UtpResolutionError(
        "Не найден подтверждённый источник тем и часов. "
        "Загрузите отдельный учебно-тематический план."
    )
