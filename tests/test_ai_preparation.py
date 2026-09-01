from pathlib import Path
from functools import lru_cache

from calendar_pedagoga.ai_preparation import prepare_ai_requests
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_requests():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name)
    content = build_content_model(build_schedule(utp), utp, program, utp_path.name)
    return prepare_ai_requests(
        build_lesson_content(content),
        program_lesson_forms=program.lesson_forms,
        program_teaching_methods=program.teaching_methods,
    )


def test_prepares_one_request_per_key_week_without_api() -> None:
    requests = _key_requests()
    assert len(requests) == 36
    assert [request.request_id for request in requests] == [
        f"week-{number:02d}" for number in range(1, 37)
    ]
    assert all(request.input.program_content for request in requests)


def test_city_requests_preserve_source_and_occurrence_context() -> None:
    city = _key_requests()[2:5]
    assert [request.week_number for request in city] == [3, 4, 5]
    assert [request.topic_occurrence_index for request in city] == [1, 2, 3]
    assert all(request.topic_occurrence_count == 3 for request in city)
    assert len({request.input.program_content for request in city}) == 1
    assert all(request.input.topic_title == "Мой город" for request in city)


def test_program_forms_and_methods_are_available_to_ai() -> None:
    request = _key_requests()[0]
    assert any("экскурсии" in value for value in request.input.program_lesson_forms)
    assert any("беседа" in value for value in request.input.program_teaching_methods)

def test_expected_response_allows_only_five_generated_fields() -> None:
    schema = _key_requests()[0].expected_response_schema
    generated = set(schema["properties"]) - {"request_id", "warnings"}
    assert generated == {
        "theory_text", "practice_text", "lesson_type",
        "planned_result", "assessment_method",
    }
    assert schema["additionalProperties"] is False


def test_zero_hour_parts_are_empty_in_prepared_rule_based_fields() -> None:
    requests = _key_requests()
    for request in requests:
        if request.input.theory_hours == 0:
            assert request.input.rule_based.theory_text == ""
        if request.input.practice_hours == 0:
            assert request.input.rule_based.practice_text == ""
