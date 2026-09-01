"""Выбор источника УТП: отдельный файл или таблица внутри программы."""

from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.parsing import UtpMetadata, UtpParseResult, parse_utp
from calendar_pedagoga.program_parsing import convert_legacy_doc
from calendar_pedagoga.upload_validation import ValidatedUpload


AUTO_WORKLOAD_WARNING = "Недельная нагрузка определена автоматически: 36 недель × 2 часа."


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


def resolve_utp(
    optional_utp_upload: ValidatedUpload | None,
    program_document: ValidatedUpload,
) -> UtpParseResult:
    """Вернуть УТП: отдельный файл имеет приоритет над таблицей внутри программы."""

    if optional_utp_upload is not None:
        parsed = optional_utp_upload.parsed
        if not isinstance(parsed, UtpParseResult):
            raise UtpResolutionError(
                "Загруженный файл УТП не содержит учебно-тематический план."
            )
        return apply_workload_from_document(parsed)

    try:
        embedded = parse_utp(_program_docx_bytes(program_document))
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
