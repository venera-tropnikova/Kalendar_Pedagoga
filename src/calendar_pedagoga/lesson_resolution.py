"""Сведение rule-based и AI-полей занятий перед генерацией DOCX."""

from __future__ import annotations

from dataclasses import dataclass

from calendar_pedagoga.ai_provider import AIBatchResult, AI_FIELDS
from calendar_pedagoga.lesson_content import (
    LessonContentRow,
    finalize_lesson_type,
    grounded_complete_week_lesson_type,
)


@dataclass(frozen=True)
class ResolvedLessonRow:
    source: LessonContentRow
    theory_text: str
    practice_text: str
    lesson_type: str
    planned_result: str
    assessment_method: str
    filled_by_ai: bool
    warnings: tuple[str, ...]


def resolve_lesson_content(
    rows: tuple[LessonContentRow, ...],
    ai_result: AIBatchResult | None = None,
    *,
    freeze_pedagogical_fields: bool = False,
) -> tuple[ResolvedLessonRow, ...]:
    """Объединить rule-based строки с проверенным AI-ответом без изменения исходных моделей.

    ``freeze_pedagogical_fields`` — CE2 FINAL gate уже закрыл TYPE/RESULT/CONTROL;
    resolution не имеет права их переписывать.
    """

    variants = (
        {variant.request_id: variant for variant in ai_result.variants}
        if ai_result
        else {}
    )
    resolved: list[ResolvedLessonRow] = []
    for row in rows:
        request_id = f"week-{row.source.week_number:02d}"
        variant = variants.get(request_id)
        warnings = list(row.warnings)
        if ai_result and variant is None:
            warnings.append("AI не вернул строку для этой недели; использованы rule-based поля.")

        if variant and not freeze_pedagogical_fields:
            theory = variant.theory_text.value or row.theory_text
            practice = variant.practice_text.value or row.practice_text
            lesson_type = finalize_lesson_type(
                variant.lesson_type.value,
                theory_hours=row.source.theory_hours,
                practice_hours=row.source.practice_hours,
                grounded_fallback=row.lesson_type,
            )
            planned_result = variant.planned_result.value or row.planned_result
            assessment = variant.assessment_method.value or row.assessment_method
            warnings.extend(variant.warnings)
            filled_by_ai = any(
                getattr(variant, field).value
                for field in AI_FIELDS
            )
        else:
            theory = row.theory_text
            practice = row.practice_text
            lesson_type = row.lesson_type
            planned_result = row.planned_result
            assessment = row.assessment_method
            filled_by_ai = False
            if not freeze_pedagogical_fields:
                lesson_type = finalize_lesson_type(
                    row.lesson_type,
                    theory_hours=row.source.theory_hours,
                    practice_hours=row.source.practice_hours,
                )

        if not freeze_pedagogical_fields:
            complete_week_type = grounded_complete_week_lesson_type(practice)
            result_low = planned_result.casefold()
            game_supported = (
                "игр" in result_low
                or result_low.startswith(
                    "выполняет практическое задание по теме"
                )
            )
            replaces_current = (
                complete_week_type
                not in {"игровое занятие", "дидактическое занятие"}
                or (game_supported and lesson_type in {
                    "практикум", "практическое занятие",
                    "теоретико-практическое занятие", "игра",
                    "игровое занятие", "дидактическое занятие",
                })
            ) and (
                complete_week_type != "игровое занятие"
                or lesson_type
                in {
                    "практикум",
                    "практическое занятие",
                    "теоретико-практическое занятие",
                    "комбинированное занятие",
                }
            )
            if complete_week_type and replaces_current:
                lesson_type = finalize_lesson_type(
                    complete_week_type,
                    theory_hours=row.source.theory_hours,
                    practice_hours=row.source.practice_hours,
                )

        if freeze_pedagogical_fields and (
            lesson_type != row.lesson_type
            or planned_result != row.planned_result
            or assessment != row.assessment_method
        ):
            raise ValueError(
                "post-gate mutation of TYPE/RESULT/CONTROL is forbidden"
            )

        resolved.append(
            ResolvedLessonRow(
                source=row,
                theory_text=theory,
                practice_text=practice,
                lesson_type=lesson_type,
                planned_result=planned_result,
                assessment_method=assessment,
                filled_by_ai=filled_by_ai,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return tuple(resolved)
