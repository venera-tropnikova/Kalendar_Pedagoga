"""Подготовка контрактов будущей AI-генерации без вызова внешнего API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from calendar_pedagoga.lesson_content import LessonContentRow


ALLOWED_SOURCE_REFS = (
    "utp_topic",
    "program_content",
    "program_lesson_forms",
    "program_teaching_methods",
    "rule_based.theory_text",
    "rule_based.practice_text",
    "rule_based.lesson_type",
    "rule_based.planned_result",
    "rule_based.assessment_method",
)


@dataclass(frozen=True)
class RuleBasedFields:
    theory_text: str
    practice_text: str
    lesson_type: str
    planned_result: str
    assessment_method: str


@dataclass(frozen=True)
class AIWeekInput:
    topic_number: str | None
    topic_title: str
    theory_hours: int
    practice_hours: int
    program_content: str
    rule_based: RuleBasedFields
    program_lesson_forms: tuple[str, ...] = ()
    program_teaching_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class AIWeekRequest:
    schema_version: str
    request_id: str
    week_number: int
    topic_occurrence_index: int
    topic_occurrence_count: int
    instructions: tuple[str, ...]
    input: AIWeekInput
    expected_response_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def expected_ai_response_schema() -> dict[str, Any]:
    sourced_field = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "source_refs"],
        "properties": {
            "value": {"type": "string"},
            "source_refs": {
                "type": "array",
                "items": {"type": "string", "enum": list(ALLOWED_SOURCE_REFS)},
            },
        },
    }
    fields = (
        "theory_text",
        "practice_text",
        "lesson_type",
        "planned_result",
        "assessment_method",
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["request_id", *fields, "warnings"],
        "properties": {
            "request_id": {"type": "string"},
            **{field: sourced_field for field in fields},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }


INSTRUCTIONS = (
    "Сформируй пять коротких полей календарного плана только из данных текущего input и общего контекста текущей образовательной программы.",
    "Конкретное предметное содержание разрешено брать только из topic_title и program_content текущей строки; сведения из других программ, тем или общих знаний запрещены.",
    "Для theory_text и practice_text используй лексически консервативное сокращение или смысловое разделение program_content: сохраняй исходные объекты и не добавляй отсутствующие определения, свойства, классификации, примеры или действия.",
    "Не заменяй исходное понятие более широким, узким или смежным понятием, даже если такая замена кажется естественной.",
    "При нулевых theory_hours или practice_hours верни пустую строку соответствующего поля и не переноси содержание между видами часов.",
    "lesson_type выбирай только из program_lesson_forms, program_teaching_methods или явно названной формы в program_content текущей программы; выбери один краткий наиболее подходящий вариант, не копируй весь перечень и не используй форму из другой программы или общих знаний.",
    "Если ни одна форма или метод текущей программы не соответствует содержанию строки, оставь lesson_type пустым и добавь warning.",
    "planned_result формулируй естественной проверяемой фразой только по объектам theory_text или practice_text этой строки; педагогический глагол результата можно вывести из содержания, не добавляя нового предметного объекта или свойства.",
    "assessment_method обязательно выводи как короткий нейтральный способ проверки сформированного planned_result; способ контроля не обязан быть дословно указан в программе, но не должен добавлять новое предметное содержание.",
    "Не меняй тему, часы, даты, request_id и week_number.",
    "Если тема занимает несколько недель, раздели её program_content по различимым смысловым фрагментам без цифровых меток и без повторения theory_text или practice_text.",
    "Для строк одной темы учитывай topic_occurrence_index и используй разные короткие формулировки; один подтверждённый фрагмент program_content можно педагогически переформулировать для разных недель, но нельзя добавлять к нему новые предметные факты.",
    "Перед ответом проверь каждую предметную фразу: если её нельзя подтвердить topic_title или точным фрагментом program_content текущей строки, удали её или верни пустое значение с warning.",
    "Для каждого непустого результата укажи точные source_refs; если доказательств недостаточно, верни пустое значение и warning.",
)

def prepare_ai_requests(
    rows: tuple[LessonContentRow, ...],
    *,
    program_lesson_forms: tuple[str, ...] = (),
    program_teaching_methods: tuple[str, ...] = (),
) -> tuple[AIWeekRequest, ...]:
    counts: dict[tuple[str | None, str], int] = {}
    for row in rows:
        key = (row.source.topic_number, row.source.topic_title)
        counts[key] = counts.get(key, 0) + 1
    seen: dict[tuple[str | None, str], int] = {}
    requests: list[AIWeekRequest] = []
    schema = expected_ai_response_schema()
    for row in rows:
        key = (row.source.topic_number, row.source.topic_title)
        seen[key] = seen.get(key, 0) + 1
        requests.append(
            AIWeekRequest(
                schema_version="1.0",
                request_id=f"week-{row.source.week_number:02d}",
                week_number=row.source.week_number,
                topic_occurrence_index=seen[key],
                topic_occurrence_count=counts[key],
                instructions=INSTRUCTIONS,
                input=AIWeekInput(
                    topic_number=row.source.topic_number,
                    topic_title=row.source.topic_title,
                    theory_hours=row.source.theory_hours,
                    practice_hours=row.source.practice_hours,
                    program_content=row.source.program_content_full,
                    rule_based=RuleBasedFields(
                        theory_text=row.theory_text,
                        practice_text=row.practice_text,
                        lesson_type=row.lesson_type,
                        planned_result=row.planned_result,
                        assessment_method=row.assessment_method,
                    ),
                    program_lesson_forms=program_lesson_forms,
                    program_teaching_methods=program_teaching_methods,
                ),
                expected_response_schema=schema,
            )
        )
    return tuple(requests)
