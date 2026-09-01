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
        if not requests:
            raise AIProviderError("Не переданы строки для AI-генерации.")
        self._ensure_shared_program_context(requests)

        accepted: dict[str, AIWeekVariant] = {}
        pending = requests
        input_tokens = 0
        output_tokens = 0
        api_calls = 0
        retries = 0
        batch_size = self.batch_size
        last_errors: dict[str, AIProviderError] = {}

        for round_index in range(self.max_retry_rounds + 1):
            if not pending:
                break
            if round_index > 0:
                retries += 1
                batch_size = max(1, min(batch_size, len(pending)))

            still_pending: list[AIWeekRequest] = []
            avoid = self._avoid_phrases(requests, accepted)
            for batch in _chunk_requests(pending, batch_size):
                api_calls += 1
                try:
                    variants, usage = self._call_single_batch(batch, avoid_phrases=avoid)
                except AIProviderError:
                    still_pending.extend(batch)
                    continue
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
                variants_by_id = {variant.request_id: variant for variant in variants}
                for request in batch:
                    variant = variants_by_id.get(request.request_id)
                    if variant is None:
                        still_pending.append(request)
                        continue
                    try:
                        self._validate_row(request, variant)
                        accepted[request.request_id] = variant
                        last_errors.pop(request.request_id, None)
                    except AIProviderError as error:
                        last_errors[request.request_id] = error
                        still_pending.append(request)
            pending = tuple(still_pending)

        if len(accepted) != len(requests):
            missing = [request.request_id for request in requests if request.request_id not in accepted]
            if missing and all(request_id in last_errors for request_id in missing):
                raise last_errors[missing[0]]
            raise AIProviderError(
                "AI не вернул полный набор строк после batch/retry: "
                + ", ".join(missing[:8])
                + ("…" if len(missing) > 8 else "")
            )

        ordered = tuple(accepted[request.request_id] for request in requests)
        conflict_round = 0
        while True:
            try:
                self._validate_cross_topic(requests, ordered)
                break
            except AIProviderError as error:
                conflicts = self._cross_topic_conflict_ids(requests, ordered)
                if not conflicts:
                    raise error
                if conflict_round >= self.max_retry_rounds:
                    ordered = self._resolve_cross_topic_duplicates(requests, ordered)
                    break
                conflict_round += 1
                retries += 1
                retry_requests = tuple(
                    request for request in requests if request.request_id in conflicts
                )
                for request in retry_requests:
                    accepted.pop(request.request_id, None)
                for request in retry_requests:
                    api_calls += 1
                    avoid = self._avoid_phrases(requests, accepted)
                    variants, usage = self._call_single_batch(
                        (request,),
                        avoid_phrases=avoid,
                    )
                    input_tokens += usage.input_tokens
                    output_tokens += usage.output_tokens
                    variant = variants[0] if variants else None
                    if variant is None or variant.request_id != request.request_id:
                        raise error
                    self._validate_row(request, variant)
                    accepted[request.request_id] = variant
                ordered = tuple(accepted[request.request_id] for request in requests)
        total_tokens = input_tokens + output_tokens
        cost = (
            input_tokens * self.INPUT_PRICE_PER_MILLION
            + output_tokens * self.OUTPUT_PRICE_PER_MILLION
        ) / 1_000_000
        return AIBatchResult(
            model=self.model,
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
        payload = {
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
