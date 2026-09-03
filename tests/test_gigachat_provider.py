import json
import os
from types import SimpleNamespace

import pytest

from calendar_pedagoga.ai_preparation import (
    AIWeekInput,
    AIWeekRequest,
    RuleBasedFields,
    expected_ai_response_schema,
)
from calendar_pedagoga.ai_provider import (
    DEFAULT_GIGACHAT_MODEL,
    GIGACHAT_PERS_SCOPE,
    GIGACHAT_SOURCE_REFS,
    AIProviderError,
    GigaChatProvider,
    OpenAIProvider,
    _parse_gigachat_json,
)


def _request(
    week: int = 1,
    *,
    theory: int = 1,
    practice: int = 1,
    content: str | None = None,
    rule_based: RuleBasedFields | None = None,
    forms: tuple[str, ...] = ("беседа",),
    methods: tuple[str, ...] = ("объяснение",),
) -> AIWeekRequest:
    return AIWeekRequest(
        "1.0",
        f"week-{week:02d}",
        week,
        1,
        1,
        ("Только источники.",),
        AIWeekInput(
            None,
            "Тема",
            theory,
            practice,
            content if content is not None else f"Текст программы {week}",
            rule_based
            or RuleBasedFields(
                "Теория из программы",
                "Практика из программы",
                "беседа",
                "Знает текст программы",
                "устный опрос",
            ),
            forms,
            methods,
        ),
        expected_ai_response_schema(),
    )


def _field(value: str, refs: list[str] | None = None) -> dict:
    if refs is None:
        refs = ["program_content"] if value else []
    return {"value": value, "source_refs": refs}


def _row(request: AIWeekRequest, *, suffix: str = "", refs: list[str] | None = None) -> dict:
    return {
        "request_id": request.request_id,
        "week_number": request.week_number,
        "theory_text": _field(f"Теория{suffix}", refs),
        "practice_text": _field(f"Практика{suffix}", refs),
        "lesson_type": _field("беседа", refs or ["program_lesson_forms"]),
        "planned_result": _field(f"Результат{suffix}", refs),
        "assessment_method": _field(f"Контроль{suffix}", refs),
        "warnings": [],
    }


def _user_content(payload) -> str:
    if isinstance(payload, str):
        return payload
    messages = getattr(payload, "messages", None)
    if messages:
        content = getattr(messages[-1], "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return str(first.get("text") or "")
            return str(getattr(first, "text", "") or "")
        return str(content or "")
    if isinstance(payload, dict) and "payload" in payload:
        return json.dumps(payload, ensure_ascii=False)
    return ""


class FakeChat:
    def __init__(self, text: str | list[str] | object):
        self._texts = text
        self.calls: list[object] = []

    def _next_text(self, payload) -> str:
        if callable(self._texts):
            return self._texts(payload, len(self.calls))
        if isinstance(self._texts, list):
            index = min(len(self.calls), len(self._texts) - 1)
            return self._texts[index]
        return str(self._texts)

    def create(self, payload=None, **kwargs):
        incoming = payload if payload is not None else kwargs
        text = self._next_text(incoming)
        self.calls.append(incoming)
        return SimpleNamespace(
            messages=[SimpleNamespace(content=[SimpleNamespace(text=text)])],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3, total_tokens=7),
        )


class FakeGigaChat:
    def __init__(
        self,
        *,
        models: tuple[str, ...] = ("GigaChat-3-Ultra",),
        chat_text: str | list[str] | object = "",
    ):
        self._models = models
        self.chat = FakeChat(chat_text)
        self.model_calls = 0

    def get_models(self):
        self.model_calls += 1
        return SimpleNamespace(data=[SimpleNamespace(id=name) for name in self._models])


def _is_fallback(variant) -> bool:
    return any(
        warning.startswith(GigaChatProvider._FALLBACK_WARNING_PREFIX)
        for warning in variant.warnings
    )


def test_gigachat_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.delenv("GIGACHAT_MODEL", raising=False)
    monkeypatch.setattr(
        "calendar_pedagoga.ai_provider._read_secret",
        lambda name: None,
    )
    with pytest.raises(AIProviderError, match="GIGACHAT_CREDENTIALS"):
        GigaChatProvider()


def test_gigachat_uses_pers_scope_and_default_model() -> None:
    client = FakeGigaChat()
    provider = GigaChatProvider(client=client)
    assert provider.scope == GIGACHAT_PERS_SCOPE
    assert provider.scope == "GIGACHAT_API_PERS"
    assert provider.model == DEFAULT_GIGACHAT_MODEL


def test_gigachat_model_setting_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-2-Max")
    provider = GigaChatProvider(client=FakeGigaChat())
    assert provider.model == "GigaChat-2-Max"


def test_gigachat_probe_connection_lists_models() -> None:
    client = FakeGigaChat(models=("GigaChat-3-Ultra", "GigaChat-2-Max"))
    names = GigaChatProvider(client=client).probe_connection()
    assert client.model_calls == 1
    assert names == ("GigaChat-3-Ultra", "GigaChat-2-Max")


def test_gigachat_generate_keeps_type_result_control_contract() -> None:
    row = {
        "request_id": "week-01",
        "week_number": 1,
        "theory_text": _field("Теория"),
        "practice_text": _field("Практика"),
        "lesson_type": _field("Тип"),
        "planned_result": _field("Результат"),
        "assessment_method": _field("Контроль"),
        "warnings": [],
    }
    client = FakeGigaChat(chat_text=json.dumps({"rows": [row]}, ensure_ascii=False))
    result = GigaChatProvider(client=client, model="GigaChat-3-Ultra").generate((_request(),))
    assert result.model == "GigaChat-3-Ultra"
    assert result.variants[0].lesson_type.value == "Тип"
    assert result.variants[0].planned_result.value == "Результат"
    assert result.variants[0].assessment_method.value == "Контроль"
    assert result.usage.estimated_cost_usd is None


@pytest.mark.skipif(
    not os.getenv("GIGACHAT_CREDENTIALS"),
    reason="нет GIGACHAT_CREDENTIALS",
)
def test_gigachat_live_connection_smoke() -> None:
    names = GigaChatProvider().probe_connection()
    assert names
    assert any("gigachat" in name.casefold() for name in names)


def test_gigachat_salvages_truncated_json_and_retries_missing_row() -> None:
    requests = tuple(_request(week) for week in (1, 2))
    complete = [_row(request, suffix=f" {request.week_number}") for request in requests]
    truncated = (
        '{"rows": ['
        + json.dumps(complete[0], ensure_ascii=False)
        + ', {"request_id": "week-02", "week_number": 2, "theory_text": {"value":'
    )
    client = FakeGigaChat(
        chat_text=[
            truncated,
            json.dumps({"rows": [complete[1]]}, ensure_ascii=False),
        ]
    )
    result = GigaChatProvider(client=client, batch_size=2, max_retry_rounds=2).generate(
        requests
    )
    assert [variant.request_id for variant in result.variants] == ["week-01", "week-02"]
    assert result.variants[0].theory_text.value == "Теория 1"
    assert result.variants[1].theory_text.value == "Теория 2"
    assert not any(_is_fallback(variant) for variant in result.variants)
    assert result.usage.retries == 1


def test_gigachat_retries_invalid_source_refs_then_uses_deterministic_fallback() -> None:
    request = _request(3)
    invalid = _row(request)
    invalid["theory_text"] = {"value": "Теория без ссылки", "source_refs": []}
    client = FakeGigaChat(chat_text=json.dumps({"rows": [invalid]}, ensure_ascii=False))
    result = GigaChatProvider(client=client, max_retry_rounds=1).generate((request,))
    variant = result.variants[0]
    assert variant.request_id == "week-03"
    assert _is_fallback(variant)
    assert variant.theory_text.value == "Теория из программы"
    assert variant.theory_text.source_refs == ("program_content",)
    assert variant.lesson_type.value == "беседа"
    assert variant.lesson_type.source_refs == ("program_lesson_forms",)
    OpenAIProvider._validate_row(request, variant)
    GigaChatProvider._validate_variant(request, variant)


def test_gigachat_fallback_does_not_add_numbers_missing_from_source() -> None:
    request = _request(
        4,
        content="Подготовка снаряжения",
        rule_based=RuleBasedFields(
            "3 вида узлов",
            "Практика из программы",
            "беседа",
            "Знает подготовку снаряжения",
            "устный опрос",
        ),
    )
    invalid = _row(request)
    invalid["theory_text"] = {"value": "Теория без ссылки", "source_refs": []}
    client = FakeGigaChat(chat_text=json.dumps({"rows": [invalid]}, ensure_ascii=False))
    result = GigaChatProvider(client=client, max_retry_rounds=0).generate((request,))
    variant = result.variants[0]
    assert _is_fallback(variant)
    assert variant.theory_text.value == ""
    assert variant.practice_text.value == "Практика из программы"
    GigaChatProvider._validate_variant(request, variant)


def test_gigachat_validator_still_rejects_missing_source_refs() -> None:
    request = _request(5)
    variant = GigaChatProvider._fallback_variant(request, "тест")
    broken = variant.__class__(
        request_id=variant.request_id,
        week_number=variant.week_number,
        theory_text=variant.theory_text.__class__("Текст без ссылки", ()),
        practice_text=variant.practice_text,
        lesson_type=variant.lesson_type,
        planned_result=variant.planned_result,
        assessment_method=variant.assessment_method,
        warnings=(),
    )
    with pytest.raises(AIProviderError, match="ссылки на источник"):
        OpenAIProvider._validate_row(request, broken)
    with pytest.raises(AIProviderError, match="недопустимую ссылку"):
        GigaChatProvider._validate_variant(
            request,
            broken.__class__(
                request_id=broken.request_id,
                week_number=broken.week_number,
                theory_text=broken.theory_text.__class__(
                    "Текст", ("rule_based.theory_text",)
                ),
                practice_text=variant.practice_text,
                lesson_type=variant.lesson_type,
                planned_result=variant.planned_result,
                assessment_method=variant.assessment_method,
                warnings=(),
            ),
        )


def test_gigachat_returns_all_weeks_when_last_batch_rows_are_missing() -> None:
    requests = tuple(
        _request(
            week,
            content=f"Текст программы {week}",
            rule_based=RuleBasedFields(
                f"Теория из программы {week}",
                f"Практика из программы {week}",
                "беседа",
                f"Знает текст программы {week}",
                "устный опрос",
            ),
        )
        for week in range(1, 37)
    )
    complete = {
        request.request_id: _row(request, suffix=f" {request.week_number}")
        for request in requests
    }

    def responder(payload, _call_index):
        wrapper = json.loads(_user_content(payload))
        requested = [row["request_id"] for row in wrapper["payload"]["rows"]]
        if len(requested) > 1:
            kept = requested[:-1]
        elif requested[0] in {"week-30", "week-36"}:
            kept = []
        else:
            kept = requested
        return json.dumps({"rows": [complete[item] for item in kept]}, ensure_ascii=False)

    client = FakeGigaChat(chat_text=responder)
    result = GigaChatProvider(client=client, batch_size=6, max_retry_rounds=2).generate(
        requests
    )
    assert [variant.week_number for variant in result.variants] == list(range(1, 37))
    fallback_weeks = [
        variant.week_number for variant in result.variants if _is_fallback(variant)
    ]
    assert fallback_weeks == [30, 36]
    for variant in result.variants:
        if _is_fallback(variant):
            assert variant.theory_text.value
            assert variant.theory_text.source_refs
            continue
        for field in (
            "theory_text",
            "practice_text",
            "lesson_type",
            "planned_result",
            "assessment_method",
        ):
            sourced = getattr(variant, field)
            if sourced.value:
                assert sourced.source_refs
                assert set(sourced.source_refs) <= set(GIGACHAT_SOURCE_REFS)


def test_parse_gigachat_json_salvages_complete_objects() -> None:
    parsed = _parse_gigachat_json(
        '{"rows": [{"request_id": "week-01", "week_number": 1}, {"request_id": "week-02"'
    )
    assert parsed["rows"] == [{"request_id": "week-01", "week_number": 1}]
