import json
from types import SimpleNamespace

import pytest

from calendar_pedagoga.ai_preparation import (
    AIWeekInput, AIWeekRequest, INSTRUCTIONS, RuleBasedFields, expected_ai_response_schema,
)
from calendar_pedagoga.ai_provider import (
    AIProviderError, OpenAIProvider,
)


def _request(week: int, theory: int, practice: int) -> AIWeekRequest:
    return AIWeekRequest(
        "1.0", f"week-{week:02d}", week, 1, 1, ("Только источники.",),
        AIWeekInput(None, "Тема", theory, practice, "Текст программы", RuleBasedFields("", "", "", "", "")),
        expected_ai_response_schema(),
    )


def _field(value: str = "") -> dict:
    return {"value": value, "source_refs": ["program_content"] if value else []}


class FakeResponses:
    def __init__(self, rows):
        self.rows = rows
        self.kwargs_list: list[dict] = []

    def create(self, **kwargs):
        self.kwargs_list.append(kwargs)
        payload = json.loads(kwargs["input"])
        requested_ids = {row["request_id"] for row in payload["rows"]}
        filtered = [row for row in self.rows if row["request_id"] in requested_ids]
        return SimpleNamespace(
            output_text=json.dumps({"rows": filtered}),
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )


def test_provider_preserves_usage_and_uses_structured_outputs() -> None:
    row = {
        "request_id": "week-02", "week_number": 2,
        "theory_text": _field("Теория"), "practice_text": _field("Практика"),
        "lesson_type": _field("Тип"), "planned_result": _field("Результат"),
        "assessment_method": _field("Контроль"), "warnings": [],
    }
    responses = FakeResponses([row])
    result = OpenAIProvider(SimpleNamespace(responses=responses)).generate((_request(2, 1, 1),))
    assert result.usage.total_tokens == 150
    assert result.usage.estimated_cost_usd == pytest.approx(0.000125)
    assert responses.kwargs_list[0]["text"]["format"]["type"] == "json_schema"
    assert responses.kwargs_list[0]["store"] is False
    assert responses.kwargs_list[0]["max_output_tokens"] == 6000


def test_provider_rejects_text_for_zero_hour_part() -> None:
    row = {
        "request_id": "week-03", "week_number": 3,
        "theory_text": _field("Теория"), "practice_text": _field("Лишняя практика"),
        "lesson_type": _field(), "planned_result": _field(),
        "assessment_method": _field(), "warnings": [],
    }
    provider = OpenAIProvider(SimpleNamespace(responses=FakeResponses([row])))
    with pytest.raises(AIProviderError, match="нулевых часах"):
        provider.generate((_request(3, 2, 0),))


def test_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AIProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider()


def test_provider_rejects_numeric_lesson_marker() -> None:
    row = {
        "request_id": "week-02", "week_number": 2,
        "theory_text": _field("История города (занятие 1)"),
        "practice_text": _field("Экскурсия"), "lesson_type": _field("Беседа"),
        "planned_result": _field("Знание истории"),
        "assessment_method": _field("Опрос"), "warnings": [],
    }
    provider = OpenAIProvider(SimpleNamespace(responses=FakeResponses([row])))
    with pytest.raises(AIProviderError, match="цифровую метку"):
        provider.generate((_request(2, 1, 1),))


def test_provider_clears_repeated_field_for_same_topic_after_retries() -> None:
    rows = [
        {
            "request_id": f"week-0{week}", "week_number": week,
            "theory_text": _field("Одинаковая теория"),
            "practice_text": _field(f"Практика {week}"),
            "lesson_type": _field(f"Тип {week}"),
            "planned_result": _field(f"Результат {week}"),
            "assessment_method": _field(f"Контроль {week}"), "warnings": [],
        }
        for week in (3, 4)
    ]
    provider = OpenAIProvider(
        SimpleNamespace(responses=FakeResponses(rows)),
        batch_size=2,
        max_retry_rounds=0,
    )
    result = provider.generate((_request(3, 1, 1), _request(4, 1, 1)))
    assert result.variants[0].theory_text.value == "Одинаковая теория"
    assert result.variants[1].theory_text.value == ""
    assert any("очищена после retry" in warning for warning in result.variants[1].warnings)
def _source_request(
    request_id: str,
    week: int,
    topic: str,
    content: str,
    forms: tuple[str, ...],
    methods: tuple[str, ...],
) -> AIWeekRequest:
    return AIWeekRequest(
        "1.0",
        request_id,
        week,
        1,
        1,
        INSTRUCTIONS,
        AIWeekInput(
            None,
            topic,
            1,
            1,
            content,
            RuleBasedFields("", "", "", "", ""),
            forms,
            methods,
        ),
        expected_ai_response_schema(),
    )


def _empty_response_row(request: AIWeekRequest) -> dict:
    return {
        "request_id": request.request_id,
        "week_number": request.week_number,
        "theory_text": _field(),
        "practice_text": _field(),
        "lesson_type": _field(),
        "planned_result": _field(),
        "assessment_method": _field(),
        "warnings": ["Недостаточно данных."],
    }


def test_generation_accepts_arbitrary_week_without_key_selector() -> None:
    request = _source_request(
        "external-47",
        47,
        "Изготовление свистульки",
        "Подготовка глины. Формование свистульки.",
        ("работа в мастерской",),
        ("показ",),
    )
    responses = FakeResponses([_empty_response_row(request)])
    result = OpenAIProvider(SimpleNamespace(responses=responses)).generate((request,))
    assert result.variants[0].week_number == 47


def test_payload_uses_only_each_program_context_without_leakage() -> None:
    first = _source_request(
        "ceramics-01",
        1,
        "Изготовление свистульки",
        "Подготовка глины. Формование свистульки.",
        ("работа в мастерской",),
        ("показ",),
    )
    second = _source_request(
        "astronomy-01",
        1,
        "Наблюдение созвездий",
        "Поиск созвездий на звёздной карте.",
        ("наблюдение",),
        ("работа с картой",),
    )
    first_responses = FakeResponses([_empty_response_row(first)])
    second_responses = FakeResponses([_empty_response_row(second)])
    OpenAIProvider(SimpleNamespace(responses=first_responses)).generate((first,))
    OpenAIProvider(SimpleNamespace(responses=second_responses)).generate((second,))
    first_payload = json.loads(first_responses.kwargs_list[0]["input"])
    second_payload = json.loads(second_responses.kwargs_list[0]["input"])
    assert first_payload["program_lesson_forms"] == ["работа в мастерской"]
    assert first_payload["program_teaching_methods"] == ["показ"]
    assert second_payload["program_lesson_forms"] == ["наблюдение"]
    assert second_payload["program_teaching_methods"] == ["работа с картой"]
    assert "созвезди" not in json.dumps(first_payload, ensure_ascii=False).casefold()
    assert "глин" not in json.dumps(second_payload, ensure_ascii=False).casefold()


def test_production_prompt_has_no_key_specific_vocabulary() -> None:
    prompt = " ".join(INSTRUCTIONS).casefold()
    forbidden = (
        "ключ",
        "аптеч",
        "лекарствен",
        "город",
        "професси",
        "родител",
        "обж",
        "памятник",
        "предприят",
        "экскурс",
    )
    assert not any(token in prompt for token in forbidden)


def test_provider_batches_large_universal_calendar_and_aggregates_usage() -> None:
    requests = tuple(_request(week, 1, 1) for week in range(1, 37))
    rows = [
        {
            "request_id": request.request_id,
            "week_number": request.week_number,
            "theory_text": _field(f"Теория {request.week_number}"),
            "practice_text": _field(f"Практика {request.week_number}"),
            "lesson_type": _field("Тип"),
            "planned_result": _field("Результат"),
            "assessment_method": _field("Контроль"),
            "warnings": [],
        }
        for request in requests
    ]
    responses = FakeResponses(rows)

    result = OpenAIProvider(
        SimpleNamespace(responses=responses),
        batch_size=6,
    ).generate(requests)

    assert len(result.variants) == 36
    assert result.usage.api_calls == 6
    assert result.usage.total_tokens == 900
    assert all(
        call["max_output_tokens"] == 6000
        for call in responses.kwargs_list
    )


def test_provider_retries_only_missing_rows_without_regenerating_accepted() -> None:
    requests = tuple(_request(week, 1, 1) for week in range(1, 4))
    complete_rows = [
        {
            "request_id": request.request_id,
            "week_number": request.week_number,
            "theory_text": _field(f"Т {request.week_number}"),
            "practice_text": _field(f"П {request.week_number}"),
            "lesson_type": _field("Тип"),
            "planned_result": _field("Результат"),
            "assessment_method": _field("Контроль"),
            "warnings": [],
        }
        for request in requests
    ]
    calls: list[list[dict]] = []

    class RetryResponses:
        def create(self, **kwargs):
            payload = json.loads(kwargs["input"])
            calls.append(payload["rows"])
            if len(calls) == 1:
                filtered = [complete_rows[0]]
            else:
                filtered = [
                    row
                    for row in complete_rows
                    if row["request_id"] in {item["request_id"] for item in payload["rows"]}
                ]
            return SimpleNamespace(
                output_text=json.dumps({"rows": filtered}),
                usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
            )

    result = OpenAIProvider(
        SimpleNamespace(responses=RetryResponses()),
        batch_size=3,
        max_retry_rounds=2,
    ).generate(requests)

    assert [variant.request_id for variant in result.variants] == [
        "week-01",
        "week-02",
        "week-03",
    ]
    assert result.usage.api_calls == 2
    assert result.usage.retries == 1
    assert len(calls[1]) == 2