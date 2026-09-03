"""Изолированный провайдер AI; не изменяет календарь и исходные модели."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Protocol

from calendar_pedagoga.ai_preparation import AIWeekRequest, ALLOWED_SOURCE_REFS


AI_FIELDS = (
    "theory_text",
    "practice_text",
    "lesson_type",
    "planned_result",
    "assessment_method",
)

# ~750 output tokens на строку; batch=6 укладывается в min 6000 output tokens.
DEFAULT_BATCH_SIZE = 6
MAX_RETRY_ROUNDS = 3
GIGACHAT_PERS_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_GIGACHAT_MODEL = "GigaChat-3-Ultra"
GIGACHAT_SOURCE_REFS = (
    "utp_topic",
    "program_content",
    "program_lesson_forms",
    "program_teaching_methods",
)


class AIProviderError(RuntimeError):
    """Контролируемая ошибка конфигурации или ответа AI-провайдера."""


@dataclass(frozen=True)
class SourcedAIValue:
    value: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class AIWeekVariant:
    request_id: str
    week_number: int
    theory_text: SourcedAIValue
    practice_text: SourcedAIValue
    lesson_type: SourcedAIValue
    planned_result: SourcedAIValue
    assessment_method: SourcedAIValue
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AIUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None
    api_calls: int = 0
    retries: int = 0


@dataclass(frozen=True)
class AIBatchResult:
    model: str
    variants: tuple[AIWeekVariant, ...]
    usage: AIUsage


@dataclass(frozen=True)
class AIComparisonRow:
    week_number: int
    rule_based: dict[str, str]
    ai_variant: AIWeekVariant
    source_program_content: str


def compare_with_rule_based(
    requests: tuple[AIWeekRequest, ...], result: AIBatchResult
) -> tuple[AIComparisonRow, ...]:
    variants = {variant.request_id: variant for variant in result.variants}
    return tuple(
        AIComparisonRow(
            week_number=request.week_number,
            rule_based={
                field: getattr(request.input.rule_based, field) for field in AI_FIELDS
            },
            ai_variant=variants[request.request_id],
            source_program_content=request.input.program_content,
        )
        for request in requests
    )


class AIProvider(Protocol):
    def generate(self, requests: tuple[AIWeekRequest, ...]) -> AIBatchResult: ...


def _sourced_schema() -> dict[str, Any]:
    return {
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


def batch_response_schema() -> dict[str, Any]:
    sourced = _sourced_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows"],
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["request_id", "week_number", *AI_FIELDS, "warnings"],
                    "properties": {
                        "request_id": {"type": "string"},
                        "week_number": {"type": "integer"},
                        **{field: sourced for field in AI_FIELDS},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def gigachat_batch_response_schema() -> dict[str, Any]:
    """Схема GigaChat без ссылок на производные deterministic-поля."""

    schema = batch_response_schema()
    properties = schema["properties"]["rows"]["items"]["properties"]
    for field in AI_FIELDS:
        properties[field]["properties"]["source_refs"]["items"]["enum"] = list(
            GIGACHAT_SOURCE_REFS
        )
    return schema


def _chunk_requests(
    requests: tuple[AIWeekRequest, ...], batch_size: int
) -> tuple[tuple[AIWeekRequest, ...], ...]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    items = list(requests)
    return tuple(
        tuple(items[index : index + batch_size])
        for index in range(0, len(items), batch_size)
    )


def _week_batch_payload(
    requests: tuple[AIWeekRequest, ...],
    *,
    avoid_phrases: dict[tuple[str | None, str], tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    avoid_phrases = avoid_phrases or {}
    return {
        "program_lesson_forms": list(requests[0].input.program_lesson_forms),
        "program_teaching_methods": list(requests[0].input.program_teaching_methods),
        "rows": [
            {
                "request_id": request.request_id,
                "week_number": request.week_number,
                "topic_occurrence_index": request.topic_occurrence_index,
                "topic_occurrence_count": request.topic_occurrence_count,
                "avoid_phrases": list(
                    avoid_phrases.get(
                        (request.input.topic_number, request.input.topic_title),
                        (),
                    )
                ),
                "input": {
                    "topic_title": request.input.topic_title,
                    "theory_hours": request.input.theory_hours,
                    "practice_hours": request.input.practice_hours,
                    "program_content": request.input.program_content,
                    "rule_based": {
                        "theory_text": request.input.rule_based.theory_text,
                        "practice_text": request.input.rule_based.practice_text,
                        "lesson_type": request.input.rule_based.lesson_type,
                        "planned_result": request.input.rule_based.planned_result,
                        "assessment_method": request.input.rule_based.assessment_method,
                    },
                },
            }
            for request in requests
        ],
    }


def _gigachat_week_batch_payload(
    requests: tuple[AIWeekRequest, ...],
    *,
    avoid_phrases: dict[tuple[str | None, str], tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Контекст GigaChat содержит только первичные данные программы и темы."""

    payload = _week_batch_payload(requests, avoid_phrases=avoid_phrases)
    for row in payload["rows"]:
        row["input"].pop("rule_based", None)
    return payload


def _run_structured_generate(
    provider: Any,
    requests: tuple[AIWeekRequest, ...],
    *,
    estimate_cost,
) -> AIBatchResult:
    """Общий batch/retry-цикл для OpenAI и GigaChat без смены контракта полей."""

    if not requests:
        raise AIProviderError("Не переданы строки для AI-генерации.")
    OpenAIProvider._ensure_shared_program_context(requests)

    accepted: dict[str, AIWeekVariant] = {}
    pending = requests
    input_tokens = 0
    output_tokens = 0
    api_calls = 0
    retries = 0
    batch_size = provider.batch_size
    last_errors: dict[str, AIProviderError] = {}

    for round_index in range(provider.max_retry_rounds + 1):
        if not pending:
            break
        if round_index > 0:
            retries += 1
            if getattr(provider, "retry_individually", False):
                batch_size = 1
            else:
                batch_size = max(1, min(batch_size, len(pending)))

        still_pending: list[AIWeekRequest] = []
        avoid = OpenAIProvider._avoid_phrases(requests, accepted)
        for batch in _chunk_requests(pending, batch_size):
            api_calls += 1
            try:
                variants, usage = provider._call_single_batch(batch, avoid_phrases=avoid)
            except AIProviderError as error:
                still_pending.extend(batch)
                for request in batch:
                    last_errors[request.request_id] = error
                continue
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            variants_by_id = {variant.request_id: variant for variant in variants}
            for request in batch:
                variant = variants_by_id.get(request.request_id)
                if variant is None:
                    last_errors[request.request_id] = AIProviderError(
                        "строка отсутствовала в ответе"
                    )
                    still_pending.append(request)
                    continue
                try:
                    provider_validator = getattr(provider, "_validate_variant", None)
                    if provider_validator is not None:
                        provider_validator(request, variant)
                    OpenAIProvider._validate_row(request, variant)
                    accepted[request.request_id] = variant
                    last_errors.pop(request.request_id, None)
                except AIProviderError as error:
                    last_errors[request.request_id] = error
                    still_pending.append(request)
        pending = tuple(still_pending)

    if len(accepted) != len(requests):
        missing = [request.request_id for request in requests if request.request_id not in accepted]
        fallback_factory = getattr(provider, "_fallback_variant", None)
        if fallback_factory is not None:
            request_by_id = {request.request_id: request for request in requests}
            for request_id in missing:
                reason = last_errors.get(request_id)
                accepted[request_id] = fallback_factory(
                    request_by_id[request_id],
                    str(reason) if reason is not None else "строка отсутствовала в ответе",
                )
        elif missing and all(request_id in last_errors for request_id in missing):
            raise last_errors[missing[0]]
        else:
            raise AIProviderError(
                "AI не вернул полный набор строк после batch/retry: "
                + ", ".join(missing[:8])
                + ("…" if len(missing) > 8 else "")
            )

    ordered = tuple(accepted[request.request_id] for request in requests)
    conflict_round = 0
    while True:
        try:
            OpenAIProvider._validate_cross_topic(requests, ordered)
            break
        except AIProviderError as error:
            conflicts = OpenAIProvider._cross_topic_conflict_ids(requests, ordered)
            if not conflicts:
                raise error
            if conflict_round >= provider.max_retry_rounds:
                ordered = OpenAIProvider._resolve_cross_topic_duplicates(requests, ordered)
                break
            conflict_round += 1
            retries += 1
            retry_requests = tuple(
                request for request in requests if request.request_id in conflicts
            )
            fallback_factory = getattr(provider, "_fallback_variant", None)
            for request in retry_requests:
                accepted.pop(request.request_id, None)
            for request in retry_requests:
                api_calls += 1
                avoid = OpenAIProvider._avoid_phrases(requests, accepted)
                try:
                    variants, usage = provider._call_single_batch(
                        (request,),
                        avoid_phrases=avoid,
                    )
                    input_tokens += usage.input_tokens
                    output_tokens += usage.output_tokens
                    variant = variants[0] if variants else None
                    if variant is None or variant.request_id != request.request_id:
                        raise AIProviderError("строка отсутствовала в ответе")
                    provider_validator = getattr(provider, "_validate_variant", None)
                    if provider_validator is not None:
                        provider_validator(request, variant)
                    OpenAIProvider._validate_row(request, variant)
                    accepted[request.request_id] = variant
                except AIProviderError as retry_error:
                    if fallback_factory is None:
                        raise error
                    accepted[request.request_id] = fallback_factory(
                        request, str(retry_error)
                    )
            ordered = tuple(accepted[request.request_id] for request in requests)
    total_tokens = input_tokens + output_tokens
    cost = estimate_cost(input_tokens, output_tokens)
    return AIBatchResult(
        model=provider.model,
        variants=ordered,
        usage=AIUsage(
            input_tokens,
            output_tokens,
            total_tokens,
            cost,
            api_calls=api_calls,
            retries=retries,
        ),
    )


class OpenAIProvider:
    """Responses API с Structured Outputs, batching и retry по строкам."""

    INPUT_PRICE_PER_MILLION = 0.25
    OUTPUT_PRICE_PER_MILLION = 2.00

    def __init__(
        self,
        client: Any | None = None,
        model: str = "gpt-5-mini",
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retry_rounds: int = MAX_RETRY_ROUNDS,
    ) -> None:
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise AIProviderError("OPENAI_API_KEY не настроен.")
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model
        self.batch_size = batch_size
        self.max_retry_rounds = max_retry_rounds

    def generate(self, requests: tuple[AIWeekRequest, ...]) -> AIBatchResult:
        return _run_structured_generate(
            self,
            requests,
            estimate_cost=lambda input_tokens, output_tokens: (
                input_tokens * self.INPUT_PRICE_PER_MILLION
                + output_tokens * self.OUTPUT_PRICE_PER_MILLION
            )
            / 1_000_000,
        )

    @staticmethod
    def _ensure_shared_program_context(requests: tuple[AIWeekRequest, ...]) -> None:
        lesson_forms = requests[0].input.program_lesson_forms
        teaching_methods = requests[0].input.program_teaching_methods
        if any(
            request.input.program_lesson_forms != lesson_forms
            or request.input.program_teaching_methods != teaching_methods
            for request in requests
        ):
            raise AIProviderError("AI-запросы содержат разный контекст образовательной программы.")

    @staticmethod
    def _avoid_phrases(
        requests: tuple[AIWeekRequest, ...],
        accepted: dict[str, AIWeekVariant],
    ) -> dict[tuple[str | None, str], tuple[str, ...]]:
        request_by_id = {request.request_id: request for request in requests}
        avoid: dict[tuple[str | None, str], list[str]] = {}
        for variant in accepted.values():
            request = request_by_id[variant.request_id]
            key = (request.input.topic_number, request.input.topic_title)
            bucket = avoid.setdefault(key, [])
            for field in ("theory_text", "practice_text"):
                value = getattr(variant, field).value.strip()
                if value:
                    bucket.append(value)
        return {key: tuple(values) for key, values in avoid.items()}

    def _call_single_batch(
        self,
        requests: tuple[AIWeekRequest, ...],
        *,
        avoid_phrases: dict[tuple[str | None, str], tuple[str, ...]] | None = None,
    ) -> tuple[tuple[AIWeekVariant, ...], AIUsage]:
        avoid_phrases = avoid_phrases or {}
        payload = _week_batch_payload(requests, avoid_phrases=avoid_phrases)
        response = self.client.responses.create(
            model=self.model,
            instructions="\n".join(requests[0].instructions),
            input=json.dumps(payload, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "calendar_ai_test_rows",
                    "strict": True,
                    "schema": batch_response_schema(),
                }
            },
            reasoning={"effort": "low"},
            max_output_tokens=max(6000, min(30000, len(requests) * 750)),
            store=False,
        )
        try:
            parsed = json.loads(response.output_text)
            variants = tuple(self._parse_row(row) for row in parsed["rows"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AIProviderError(f"Некорректный JSON-ответ AI: {error}") from error
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
        cost = (
            input_tokens * self.INPUT_PRICE_PER_MILLION
            + output_tokens * self.OUTPUT_PRICE_PER_MILLION
        ) / 1_000_000
        return variants, AIUsage(input_tokens, output_tokens, total_tokens, cost)

    @staticmethod
    def _parse_row(row: dict[str, Any]) -> AIWeekVariant:
        def sourced(name: str) -> SourcedAIValue:
            value = row[name]
            return SourcedAIValue(value["value"].strip(), tuple(value["source_refs"]))

        return AIWeekVariant(
            request_id=row["request_id"],
            week_number=int(row["week_number"]),
            theory_text=sourced("theory_text"),
            practice_text=sourced("practice_text"),
            lesson_type=sourced("lesson_type"),
            planned_result=sourced("planned_result"),
            assessment_method=sourced("assessment_method"),
            warnings=tuple(row["warnings"]),
        )

    @staticmethod
    def _validate_row(request: AIWeekRequest, variant: AIWeekVariant) -> None:
        if variant.request_id != request.request_id:
            raise AIProviderError("AI вернул строку с чужим request_id.")
        if variant.week_number != request.week_number:
            raise AIProviderError("AI изменил номер недели.")
        if request.input.theory_hours == 0 and variant.theory_text.value:
            raise AIProviderError("AI заполнил теорию при нулевых часах.")
        if request.input.practice_hours == 0 and variant.practice_text.value:
            raise AIProviderError("AI заполнил практику при нулевых часах.")
        if (
            variant.theory_text.value
            and variant.theory_text.value == variant.practice_text.value
        ):
            raise AIProviderError("AI повторил один текст в теории и практике.")
        for field in AI_FIELDS:
            sourced = getattr(variant, field)
            if sourced.value and not sourced.source_refs:
                raise AIProviderError("Непустое AI-поле не содержит ссылки на источник.")
            if len(sourced.source_refs) != len(set(sourced.source_refs)):
                raise AIProviderError("AI продублировал ссылку на источник.")
            if field == "lesson_type" and sourced.value and not (
                set(sourced.source_refs)
                & {"program_lesson_forms", "program_teaching_methods", "program_content"}
            ):
                raise AIProviderError("Тип занятия не подтверждён образовательной программой.")
            if re.search(r"\bзанятие\s*(?:№\s*)?\d+\b", sourced.value, re.IGNORECASE):
                raise AIProviderError("AI добавил запрещённую цифровую метку занятия.")

    @staticmethod
    def _cross_topic_conflict_ids(
        requests: tuple[AIWeekRequest, ...], variants: tuple[AIWeekVariant, ...]
    ) -> set[str]:
        request_by_id = {request.request_id: request for request in requests}
        grouped: dict[tuple[str | None, str, str], dict[str, list[str]]] = {}
        for variant in variants:
            request = request_by_id[variant.request_id]
            for field in ("theory_text", "practice_text"):
                value = getattr(variant, field).value.strip().casefold()
                if not value:
                    continue
                key = (request.input.topic_number, request.input.topic_title, field)
                grouped.setdefault(key, {}).setdefault(value, []).append(variant.request_id)
        conflicts: set[str] = set()
        for duplicates in grouped.values():
            for request_ids in duplicates.values():
                if len(request_ids) > 1:
                    conflicts.update(request_ids)
        return conflicts

    @staticmethod
    def _resolve_cross_topic_duplicates(
        requests: tuple[AIWeekRequest, ...],
        variants: tuple[AIWeekVariant, ...],
    ) -> tuple[AIWeekVariant, ...]:
        """После исчерпания retry: оставить первую формулировку, дубликаты очистить."""

        request_by_id = {request.request_id: request for request in requests}
        seen: dict[tuple[str | None, str, str], set[str]] = {}
        resolved: list[AIWeekVariant] = []
        for variant in variants:
            request = request_by_id[variant.request_id]
            fields = {
                field: getattr(variant, field)
                for field in AI_FIELDS
            }
            warnings = list(variant.warnings)
            for field in ("theory_text", "practice_text"):
                value = fields[field].value.strip().casefold()
                if not value:
                    continue
                key = (request.input.topic_number, request.input.topic_title, field)
                values = seen.setdefault(key, set())
                if value in values:
                    fields[field] = SourcedAIValue("", ())
                    warnings.append(
                        "Повторная AI-формулировка для этой темы очищена после retry."
                    )
                else:
                    values.add(value)
            resolved.append(
                AIWeekVariant(
                    request_id=variant.request_id,
                    week_number=variant.week_number,
                    theory_text=fields["theory_text"],
                    practice_text=fields["practice_text"],
                    lesson_type=fields["lesson_type"],
                    planned_result=fields["planned_result"],
                    assessment_method=fields["assessment_method"],
                    warnings=tuple(dict.fromkeys(warnings)),
                )
            )
        return tuple(resolved)

    @staticmethod
    def _validate_cross_topic(
        requests: tuple[AIWeekRequest, ...], variants: tuple[AIWeekVariant, ...]
    ) -> None:
        request_by_id = {request.request_id: request for request in requests}
        if set(request_by_id) != {variant.request_id for variant in variants}:
            raise AIProviderError("AI вернул неполный или посторонний набор строк.")
        seen: dict[tuple[str | None, str, str], set[str]] = {}
        for variant in variants:
            request = request_by_id[variant.request_id]
            for field in ("theory_text", "practice_text"):
                value = getattr(variant, field).value.strip().casefold()
                if not value:
                    continue
                key = (request.input.topic_number, request.input.topic_title, field)
                values = seen.setdefault(key, set())
                if value in values:
                    raise AIProviderError(
                        "AI повторил одинаковую формулировку в разных неделях одной темы."
                    )
                values.add(value)


def _read_secret(name: str) -> str | None:
    """Ключ/настройка только из env или Streamlit secrets, не из кода."""

    raw = os.getenv(name)
    if raw and raw.strip():
        return raw.strip()
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        try:
            import streamlit as st

            raw = st.secrets.get(name)
        except Exception:
            raw = None
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().casefold() not in {"0", "false", "no", "off"}


def _gigachat_message_text(response: Any) -> str:
    messages = getattr(response, "messages", None)
    if messages:
        content = getattr(messages[0], "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if content:
            first = content[0]
            text = getattr(first, "text", None)
            if text:
                return str(text).strip()
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise AIProviderError("GigaChat вернул ответ без текста.")


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _salvage_gigachat_rows(text: str) -> list[dict[str, Any]]:
    """Достать завершённые объекты rows из обрезанного JSON GigaChat."""

    marker = text.find('"rows"')
    if marker < 0:
        return []
    bracket = text.find("[", marker)
    if bracket < 0:
        return []
    decoder = json.JSONDecoder()
    rows: list[dict[str, Any]] = []
    index = bracket + 1
    length = len(text)
    while index < length:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length or text[index] == "]":
            break
        if text[index] != "{":
            break
        try:
            obj, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            rows.append(obj)
        index = end
    return rows


def _normalize_gigachat_payload(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, list):
        return {"rows": parsed}
    if not isinstance(parsed, dict):
        raise AIProviderError("Некорректный JSON-ответ GigaChat: ожидался объект.")
    if "rows" not in parsed and "request_id" in parsed:
        return {"rows": [parsed]}
    return parsed


def _parse_gigachat_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return _normalize_gigachat_payload(json.loads(cleaned))
    except json.JSONDecodeError as error:
        salvaged = _salvage_gigachat_rows(cleaned)
        if salvaged:
            return {"rows": salvaged}
        extracted = _extract_json_object(cleaned)
        if extracted != cleaned:
            try:
                return _normalize_gigachat_payload(json.loads(extracted))
            except json.JSONDecodeError:
                salvaged = _salvage_gigachat_rows(extracted)
                if salvaged:
                    return {"rows": salvaged}
        raise AIProviderError(f"Некорректный JSON-ответ GigaChat: {error}") from error


def _coerce_source_refs(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        text = raw.strip()
        return (text,) if text else ()
    if isinstance(raw, (list, tuple)):
        return tuple(
            item.strip()
            for item in raw
            if isinstance(item, str) and item.strip()
        )
    return ()


def _parse_gigachat_sourced(row: dict[str, Any], name: str) -> SourcedAIValue:
    value = row.get(name)
    if value is None:
        return SourcedAIValue("", ())
    if isinstance(value, str):
        return SourcedAIValue(value.strip(), ())
    if not isinstance(value, dict):
        raise AIProviderError(f"GigaChat вернул некорректное поле {name}.")
    text = str(value.get("value") or "").strip()
    refs = _coerce_source_refs(value.get("source_refs", value.get("source_ref")))
    return SourcedAIValue(text, refs)


def _parse_gigachat_row(row: Any) -> AIWeekVariant:
    if not isinstance(row, dict):
        raise AIProviderError("GigaChat вернул некорректную строку.")
    request_id = str(row.get("request_id") or "").strip()
    if not request_id:
        raise AIProviderError("GigaChat вернул строку без request_id.")
    try:
        week_number = int(row["week_number"])
    except (KeyError, TypeError, ValueError) as error:
        raise AIProviderError("GigaChat вернул строку без номера недели.") from error
    warnings_raw = row.get("warnings") or ()
    if isinstance(warnings_raw, str):
        warnings: tuple[str, ...] = (warnings_raw,) if warnings_raw.strip() else ()
    else:
        warnings = tuple(str(item) for item in warnings_raw if str(item).strip())
    return AIWeekVariant(
        request_id=request_id,
        week_number=week_number,
        theory_text=_parse_gigachat_sourced(row, "theory_text"),
        practice_text=_parse_gigachat_sourced(row, "practice_text"),
        lesson_type=_parse_gigachat_sourced(row, "lesson_type"),
        planned_result=_parse_gigachat_sourced(row, "planned_result"),
        assessment_method=_parse_gigachat_sourced(row, "assessment_method"),
        warnings=warnings,
    )


def _parse_gigachat_variants(
    parsed: dict[str, Any], expected_ids: set[str]
) -> tuple[AIWeekVariant, ...]:
    raw_rows = parsed.get("rows")
    if not isinstance(raw_rows, list):
        raise AIProviderError("Некорректный JSON-ответ GigaChat: нет массива rows.")
    variants: list[AIWeekVariant] = []
    seen: set[str] = set()
    for row in raw_rows:
        try:
            variant = _parse_gigachat_row(row)
        except AIProviderError:
            continue
        if variant.request_id not in expected_ids or variant.request_id in seen:
            continue
        seen.add(variant.request_id)
        variants.append(variant)
    return tuple(variants)


def _request_source_text(request: AIWeekRequest) -> str:
    return " ".join(
        (
            request.input.topic_title,
            request.input.program_content,
            *request.input.program_lesson_forms,
            *request.input.program_teaching_methods,
        )
    )


def _request_source_numbers(request: AIWeekRequest) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", _request_source_text(request)))


def _gigachat_max_output_tokens(row_count: int) -> int:
    return max(4000, min(16000, row_count * 800))


class GigaChatProvider:
    """Официальный SDK gigachat; тот же контракт type/result/control, что у OpenAI."""

    scope = GIGACHAT_PERS_SCOPE
    retry_individually = True
    _FALLBACK_WARNING_PREFIX = "GigaChat fallback: "

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retry_rounds: int = MAX_RETRY_ROUNDS,
    ) -> None:
        resolved_model = model or _read_secret("GIGACHAT_MODEL") or DEFAULT_GIGACHAT_MODEL
        if client is None:
            credentials = _read_secret("GIGACHAT_CREDENTIALS")
            if not credentials:
                raise AIProviderError("GIGACHAT_CREDENTIALS не настроен.")
            from gigachat import GigaChat

            client = GigaChat(
                credentials=credentials,
                scope=GIGACHAT_PERS_SCOPE,
                model=resolved_model,
                verify_ssl_certs=_read_flag("GIGACHAT_VERIFY_SSL_CERTS", True),
                timeout=120.0,
            )
        self.client = client
        self.model = resolved_model
        self.batch_size = batch_size
        self.max_retry_rounds = max_retry_rounds

    def probe_connection(self) -> tuple[str, ...]:
        """Проверка OAuth и доступа к API: список моделей, без генерации календаря."""

        try:
            payload = self.client.get_models()
        except Exception as error:
            raise AIProviderError(f"Не удалось подключиться к GigaChat: {error}") from error
        data = getattr(payload, "data", payload) or ()
        names: list[str] = []
        for item in data:
            model_id = getattr(item, "id_", None) or getattr(item, "id", None)
            if model_id:
                names.append(str(model_id))
        if not names:
            raise AIProviderError("GigaChat не вернул список моделей.")
        return tuple(names)

    def generate(self, requests: tuple[AIWeekRequest, ...]) -> AIBatchResult:
        return _run_structured_generate(
            self,
            requests,
            estimate_cost=lambda _input_tokens, _output_tokens: None,
        )

    @staticmethod
    def _validate_variant(request: AIWeekRequest, variant: AIWeekVariant) -> None:
        for field in AI_FIELDS:
            refs = getattr(variant, field).source_refs
            if any(ref not in GIGACHAT_SOURCE_REFS for ref in refs):
                raise AIProviderError(
                    "GigaChat вернул недопустимую ссылку на источник."
                )

        source_numbers = _request_source_numbers(request)
        for field in AI_FIELDS:
            value = getattr(variant, field).value
            unsupported = set(re.findall(r"\d+(?:[.,]\d+)?", value)) - source_numbers
            if unsupported:
                raise AIProviderError(
                    "GigaChat добавил число, отсутствующее в источнике."
                )

    @staticmethod
    def _fallback_refs(request: AIWeekRequest, field: str, value: str) -> tuple[str, ...]:
        if not value:
            return ()
        folded = value.casefold()
        if field == "lesson_type":
            if any(
                folded == item.casefold() for item in request.input.program_lesson_forms
            ):
                return ("program_lesson_forms",)
            if any(
                folded == item.casefold()
                for item in request.input.program_teaching_methods
            ):
                return ("program_teaching_methods",)
            if folded in request.input.program_content.casefold():
                return ("program_content",)
            return ()
        if request.input.program_content.strip():
            return ("program_content",)
        if request.input.topic_title.strip():
            return ("utp_topic",)
        return ()

    @classmethod
    def _fallback_field(cls, request: AIWeekRequest, field: str) -> SourcedAIValue:
        raw = getattr(request.input.rule_based, field).strip()
        if not raw:
            return SourcedAIValue("", ())
        if field == "theory_text" and request.input.theory_hours == 0:
            return SourcedAIValue("", ())
        if field == "practice_text" and request.input.practice_hours == 0:
            return SourcedAIValue("", ())
        if re.search(r"\bзанятие\s*(?:№\s*)?\d+\b", raw, re.IGNORECASE):
            return SourcedAIValue("", ())
        unsupported = set(re.findall(r"\d+(?:[.,]\d+)?", raw)) - _request_source_numbers(
            request
        )
        if unsupported:
            return SourcedAIValue("", ())
        refs = cls._fallback_refs(request, field, raw)
        if not refs:
            return SourcedAIValue("", ())
        return SourcedAIValue(raw, refs)

    @classmethod
    def _fallback_variant(
        cls,
        request: AIWeekRequest,
        reason: str,
    ) -> AIWeekVariant:
        fields = {field: cls._fallback_field(request, field) for field in AI_FIELDS}
        if (
            fields["theory_text"].value
            and fields["theory_text"].value == fields["practice_text"].value
        ):
            if request.input.practice_hours == 0:
                fields["practice_text"] = SourcedAIValue("", ())
            else:
                fields["theory_text"] = SourcedAIValue("", ())
        return AIWeekVariant(
            request_id=request.request_id,
            week_number=request.week_number,
            theory_text=fields["theory_text"],
            practice_text=fields["practice_text"],
            lesson_type=fields["lesson_type"],
            planned_result=fields["planned_result"],
            assessment_method=fields["assessment_method"],
            warnings=(cls._FALLBACK_WARNING_PREFIX + reason,),
        )

    def _call_single_batch(
        self,
        requests: tuple[AIWeekRequest, ...],
        *,
        avoid_phrases: dict[tuple[str | None, str], tuple[str, ...]] | None = None,
    ) -> tuple[tuple[AIWeekVariant, ...], AIUsage]:
        payload = _gigachat_week_batch_payload(requests, avoid_phrases=avoid_phrases)
        user_content = json.dumps(
            {
                "response_schema": gigachat_batch_response_schema(),
                "payload": payload,
            },
            ensure_ascii=False,
        )
        instructions = "\n".join(
            (
                *requests[0].instructions,
                "Для source_refs используй только точные значения utp_topic, program_content, program_lesson_forms или program_teaching_methods; любые другие значения, включая rule_based и rule_based.*, запрещены.",
                "Если поле нельзя подтвердить этими источниками, верни пустое value, пустой source_refs и добавь warning; не придумывай источник.",
                f"Верни ровно {len(requests)} объект(а/ов) rows с теми же request_id и week_number, что в payload; не пропускай последние недели.",
                "Верни только JSON-объект с ключом rows по response_schema, без markdown.",
            )
        )
        try:
            response = self._create_chat(
                instructions,
                user_content,
                max_tokens=_gigachat_max_output_tokens(len(requests)),
            )
            parsed = _parse_gigachat_json(_gigachat_message_text(response))
            variants = _parse_gigachat_variants(
                parsed, {request.request_id for request in requests}
            )
        except AIProviderError:
            raise
        except Exception as error:
            raise AIProviderError(f"Некорректный JSON-ответ AI: {error}") from error
        usage = getattr(response, "usage", None)
        input_tokens = int(
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", 0)
            or 0
        )
        output_tokens = int(
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "output_tokens", 0)
            or 0
        )
        total_tokens = int(
            getattr(usage, "total_tokens", None) or (input_tokens + output_tokens) or 0
        )
        return variants, AIUsage(input_tokens, output_tokens, total_tokens, None)

    def _create_chat(
        self,
        instructions: str,
        user_content: str,
        *,
        max_tokens: int | None = None,
    ) -> Any:
        chat_api = getattr(self.client, "chat", None)
        create = getattr(chat_api, "create", None) if chat_api is not None else None
        response_format = {
            "type": "json_schema",
            "schema": gigachat_batch_response_schema(),
            "strict": True,
        }
        if callable(create):
            try:
                from gigachat.models.chat_completions import (
                    ChatCompletionRequest,
                    ChatMessage,
                    ChatModelOptions,
                    ChatResponseFormat,
                )

                options = ChatModelOptions()
                if max_tokens is not None:
                    options.max_tokens = max_tokens
                options.response_format = ChatResponseFormat(
                    type="json_schema",
                    schema=gigachat_batch_response_schema(),
                    strict=True,
                )
                request = ChatCompletionRequest(
                    model=self.model,
                    messages=(
                        ChatMessage(role="system", content=instructions),
                        ChatMessage(role="user", content=user_content),
                    ),
                    model_options=options,
                )
            except Exception:
                return create(user_content)
            try:
                return create(request)
            except Exception:
                request.model_options = (
                    ChatModelOptions(max_tokens=max_tokens)
                    if max_tokens is not None
                    else None
                )
                return create(request)
        if callable(chat_api):
            try:
                from gigachat.models import Chat, Messages, MessagesRole
            except Exception as error:
                raise AIProviderError(
                    "Клиент GigaChat не поддерживает chat API."
                ) from error
            messages = [
                Messages(role=MessagesRole.SYSTEM, content=instructions),
                Messages(role=MessagesRole.USER, content=user_content),
            ]
            base = {"model": self.model, "messages": messages}
            if max_tokens is not None:
                base["max_tokens"] = max_tokens
            try:
                return chat_api(Chat(**base, response_format=response_format))
            except Exception:
                return chat_api(Chat(**base))
        raise AIProviderError("Клиент GigaChat не поддерживает chat API.")
