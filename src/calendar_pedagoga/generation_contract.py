"""Versioned wire contract for remote calendar generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlan,
    _confirmed_plan,
)
from calendar_pedagoga.generator_revision import generator_revision
from calendar_pedagoga.parsing import HourValue, Hours, Topic, UtpMetadata
from calendar_pedagoga.program_parsing import ProgramContentItem
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    SemanticReviewCase,
)


PIPELINE_CONTRACT = 1
MAX_PROGRAM_OVERLAY_ITEMS = 500
MAX_PROGRAM_ITEM_CONTENT_CHARS = 200_000
MAX_PROGRAM_OVERLAY_TOTAL_CHARS = 2_000_000
MAX_MATCH_REVIEW_RECORDS = 500


class GenerationContractError(ValueError):
    """A generation payload violates the public wire contract."""


def current_generator_revision() -> str:
    return generator_revision()


def _hour_to_wire(value: HourValue) -> str:
    if isinstance(value, bool):
        raise GenerationContractError("Значение часов не может быть логическим.")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise GenerationContractError("Значение часов должно быть конечным.")
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    raise GenerationContractError("Часы должны передаваться как int или Decimal модели.")


def _hour_from_wire(value: object, *, field: str) -> HourValue:
    if not isinstance(value, str):
        raise GenerationContractError(
            f"Поле «{field}» должно передаваться JSON-строкой без float."
        )
    token = value.strip()
    if not token:
        raise GenerationContractError(f"Поле «{field}» пусто.")
    try:
        parsed = Decimal(token)
    except InvalidOperation as error:
        raise GenerationContractError(f"Поле «{field}» содержит неверное число.") from error
    if not parsed.is_finite():
        raise GenerationContractError(f"Поле «{field}» должно быть конечным.")
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return parsed.normalize()


def _required_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GenerationContractError(f"Поле «{field}» должно быть объектом.")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GenerationContractError(f"Поле «{field}» обязательно.")
    return value.strip()


@dataclass(frozen=True)
class ConfirmedStudyPlanDTO:
    """Lossless JSON projection of :class:`ConfirmedStudyPlan`."""

    payload: Mapping[str, Any]

    @classmethod
    def from_model(cls, plan: ConfirmedStudyPlan) -> "ConfirmedStudyPlanDTO":
        metadata = plan._reference_metadata
        topics = [
            {
                "number": topic.number,
                "title": topic.title,
                "parent_section": topic.parent_section,
                "is_standalone_section": topic.is_standalone_section,
                "hours": {
                    "total": _hour_to_wire(topic.hours.total),
                    "theory": _hour_to_wire(topic.hours.theory),
                    "practice": _hour_to_wire(topic.hours.practice),
                },
            }
            for topic in plan.topics
        ]
        return cls(
            {
                "study_year": plan.study_year,
                "topics": topics,
                "total_hours": _hour_to_wire(plan.total_hours),
                "theory_hours": _hour_to_wire(plan.theory_hours),
                "practice_hours": _hour_to_wire(plan.practice_hours),
                "study_weeks": plan.study_weeks,
                "hours_per_week": _hour_to_wire(plan.hours_per_week),
                "source": plan.source,
                "reference_metadata": {
                    "program_name": metadata.program_name,
                    "academic_year": metadata.academic_year,
                    "study_year": metadata.study_year,
                    "student_age": metadata.student_age,
                    "hours_per_week": (
                        _hour_to_wire(metadata.hours_per_week)
                        if metadata.hours_per_week is not None
                        else None
                    ),
                    "hours_per_year": (
                        _hour_to_wire(metadata.hours_per_year)
                        if metadata.hours_per_year is not None
                        else None
                    ),
                    "study_weeks": metadata.study_weeks,
                    "teacher_name": metadata.teacher_name,
                    "stated_schedule_hours": (
                        _hour_to_wire(metadata.stated_schedule_hours)
                        if metadata.stated_schedule_hours is not None
                        else None
                    ),
                    "workload_provenance": metadata.workload_provenance,
                },
                "reference_warnings": list(plan._reference_warnings),
            }
        )

    @classmethod
    def from_dict(cls, value: object) -> "ConfirmedStudyPlanDTO":
        return cls(dict(_required_mapping(value, field="plan")))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def to_model(self) -> ConfirmedStudyPlan:
        value = self.payload
        topics_value = value.get("topics")
        if not isinstance(topics_value, list):
            raise GenerationContractError("Поле «topics» должно быть массивом.")
        topics: list[Topic] = []
        for index, item in enumerate(topics_value, start=1):
            topic = _required_mapping(item, field=f"topics[{index}]")
            hours = _required_mapping(topic.get("hours"), field=f"topics[{index}].hours")
            topics.append(
                Topic(
                    number=(str(topic["number"]) if topic.get("number") is not None else None),
                    title=_required_text(topic.get("title"), field=f"topics[{index}].title"),
                    hours=Hours(
                        _hour_from_wire(hours.get("total"), field=f"topics[{index}].total"),
                        _hour_from_wire(hours.get("theory"), field=f"topics[{index}].theory"),
                        _hour_from_wire(hours.get("practice"), field=f"topics[{index}].practice"),
                    ),
                    parent_section=(
                        str(topic["parent_section"])
                        if topic.get("parent_section") is not None
                        else None
                    ),
                    is_standalone_section=bool(topic.get("is_standalone_section", False)),
                )
            )

        metadata_value = _required_mapping(
            value.get("reference_metadata", {}), field="reference_metadata"
        )

        def optional_hour(name: str) -> HourValue | None:
            raw = metadata_value.get(name)
            return None if raw is None else _hour_from_wire(raw, field=name)

        metadata = UtpMetadata(
            program_name=metadata_value.get("program_name"),
            academic_year=metadata_value.get("academic_year"),
            study_year=metadata_value.get("study_year"),
            student_age=metadata_value.get("student_age"),
            hours_per_week=optional_hour("hours_per_week"),
            hours_per_year=optional_hour("hours_per_year"),
            study_weeks=metadata_value.get("study_weeks"),
            teacher_name=metadata_value.get("teacher_name"),
            stated_schedule_hours=optional_hour("stated_schedule_hours"),
            workload_provenance=metadata_value.get("workload_provenance"),
        )
        source = value.get("source")
        if source not in {"external_utp", "manual"}:
            raise GenerationContractError("Поле «source» имеет неизвестное значение.")
        warnings = value.get("reference_warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
            raise GenerationContractError("Поле «reference_warnings» должно быть массивом строк.")
        try:
            study_year = int(value.get("study_year"))
            study_weeks = int(value.get("study_weeks"))
        except (TypeError, ValueError) as error:
            raise GenerationContractError(
                "Год обучения и количество недель должны быть целыми числами."
            ) from error
        return _confirmed_plan(
            study_year=study_year,
            topics=tuple(topics),
            total_hours=_hour_from_wire(value.get("total_hours"), field="total_hours"),
            theory_hours=_hour_from_wire(value.get("theory_hours"), field="theory_hours"),
            practice_hours=_hour_from_wire(value.get("practice_hours"), field="practice_hours"),
            study_weeks=study_weeks,
            hours_per_week=_hour_from_wire(value.get("hours_per_week"), field="hours_per_week"),
            source=source,
            reference_metadata=metadata,
            reference_warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class ManualSemanticConfirmationDTO:
    review_id: str
    source_fingerprint: str
    planned_result: str
    assessment_method: str

    @classmethod
    def from_model(
        cls, value: ManualSemanticConfirmation
    ) -> "ManualSemanticConfirmationDTO":
        return cls(
            value.review_id,
            value.source_fingerprint,
            value.planned_result,
            value.assessment_method,
        )

    @classmethod
    def from_dict(cls, value: object) -> "ManualSemanticConfirmationDTO":
        item = _required_mapping(value, field="confirmation")
        return cls(
            _required_text(item.get("review_id"), field="review_id"),
            _required_text(item.get("source_fingerprint"), field="source_fingerprint"),
            _required_text(item.get("planned_result"), field="planned_result"),
            _required_text(item.get("assessment_method"), field="assessment_method"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "review_id": self.review_id,
            "source_fingerprint": self.source_fingerprint,
            "planned_result": self.planned_result,
            "assessment_method": self.assessment_method,
        }

    def to_model(self) -> ManualSemanticConfirmation:
        return ManualSemanticConfirmation(**self.to_dict())


@dataclass(frozen=True)
class SemanticReviewCaseDTO:
    payload: Mapping[str, Any]

    @classmethod
    def from_model(cls, value: SemanticReviewCase) -> "SemanticReviewCaseDTO":
        return cls(
            {
                "review_id": value.review_id,
                "source_fingerprint": value.source_fingerprint,
                "week_number": value.week_number,
                "topic_title": value.topic_title,
                "program_source": value.program_source,
                "required_clauses": list(value.required_clauses),
                "proposed_result": value.proposed_result,
                "proposed_control": value.proposed_control,
                "reasons": list(value.reasons),
                "status": value.status,
            }
        )

    @classmethod
    def from_dict(cls, value: object) -> "SemanticReviewCaseDTO":
        return cls(dict(_required_mapping(value, field="review_case")))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def to_model(self) -> SemanticReviewCase:
        value = self.payload
        required = value.get("required_clauses")
        reasons = value.get("reasons")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise GenerationContractError("required_clauses должен быть массивом строк.")
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise GenerationContractError("reasons должен быть массивом строк.")
        return SemanticReviewCase(
            review_id=_required_text(value.get("review_id"), field="review_id"),
            source_fingerprint=_required_text(
                value.get("source_fingerprint"), field="source_fingerprint"
            ),
            week_number=int(value.get("week_number")),
            topic_title=_required_text(value.get("topic_title"), field="topic_title"),
            program_source=str(value.get("program_source") or ""),
            required_clauses=tuple(required),
            proposed_result=str(value.get("proposed_result") or ""),
            proposed_control=str(value.get("proposed_control") or ""),
            reasons=tuple(reasons),
            status="REVIEW_REQUIRED",
        )


def _optional_identity_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GenerationContractError(f"Поле «{field}» должно быть строкой или null.")
    text = value.strip()
    return text or None


def _optional_study_year(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenerationContractError("Поле «study_year» должно быть целым числом или null.")
    return value


@dataclass(frozen=True)
class ProgramContentItemDTO:
    """JSON projection of one confirmed SOURCE item."""

    number: str | None
    title: str
    content: str
    parent_section: str | None
    study_year: int | None

    @classmethod
    def from_model(cls, item: ProgramContentItem) -> "ProgramContentItemDTO":
        return cls(
            number=item.number,
            title=item.title,
            content=item.content,
            parent_section=item.parent_section,
            study_year=item.study_year,
        )

    @classmethod
    def from_dict(cls, value: object) -> "ProgramContentItemDTO":
        item = _required_mapping(value, field="program_overlay.item")
        title = _required_text(item.get("title"), field="title")
        content = item.get("content")
        if not isinstance(content, str):
            raise GenerationContractError("Поле «content» должно быть строкой.")
        if len(content) > MAX_PROGRAM_ITEM_CONTENT_CHARS:
            raise GenerationContractError(
                "Содержание пункта overlay превышает допустимый размер."
            )
        return cls(
            number=_optional_identity_text(item.get("number"), field="number"),
            title=title,
            content=content,
            parent_section=_optional_identity_text(
                item.get("parent_section"), field="parent_section"
            ),
            study_year=_optional_study_year(item.get("study_year")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "content": self.content,
            "parent_section": self.parent_section,
            "study_year": self.study_year,
        }

    def to_model(self) -> ProgramContentItem:
        return ProgramContentItem(
            number=self.number,
            title=self.title,
            content=self.content,
            parent_section=self.parent_section,
            study_year=self.study_year,
        )


def encode_program_overlay(items: Sequence[ProgramContentItem]) -> dict[str, Any]:
    encoded = [ProgramContentItemDTO.from_model(item).to_dict() for item in items]
    if len(encoded) > MAX_PROGRAM_OVERLAY_ITEMS:
        raise GenerationContractError("Слишком много пунктов program_overlay.")
    return {"items": encoded}


def decode_program_overlay(value: object) -> tuple[ProgramContentItem, ...]:
    payload = _required_mapping(value, field="program_overlay")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise GenerationContractError("Поле «program_overlay.items» должно быть массивом.")
    if len(raw_items) > MAX_PROGRAM_OVERLAY_ITEMS:
        raise GenerationContractError("Слишком много пунктов program_overlay.")
    items: list[ProgramContentItem] = []
    titles: set[str] = set()
    total_chars = 0
    for raw in raw_items:
        dto = ProgramContentItemDTO.from_dict(raw)
        if dto.title in titles:
            raise GenerationContractError(
                "Пункты program_overlay не должны повторять название."
            )
        titles.add(dto.title)
        total_chars += len(dto.content)
        if total_chars > MAX_PROGRAM_OVERLAY_TOTAL_CHARS:
            raise GenerationContractError(
                "Суммарный размер program_overlay превышает допустимый."
            )
        items.append(dto.to_model())
    if not items:
        raise GenerationContractError("program_overlay.items не должен быть пустым.")
    return tuple(items)


def encode_match_reviews(reviews: Mapping | None) -> list[dict[str, Any]]:
    if not reviews:
        return []
    if len(reviews) > MAX_MATCH_REVIEW_RECORDS:
        raise GenerationContractError("Слишком много match_reviews.")
    encoded: list[dict[str, Any]] = []
    for key, value in reviews.items():
        if not (isinstance(key, tuple) and len(key) == 3):
            raise GenerationContractError(
                "Ключ match_reviews должен быть составным (number, title, parent_section)."
            )
        if not isinstance(value, Mapping):
            raise GenerationContractError("Значение match_reviews должно быть объектом.")
        number, title, parent_section = key
        if title is None or not str(title).strip():
            raise GenerationContractError("Поле «topic_title» обязательно.")
        if number is not None and not isinstance(number, str):
            raise GenerationContractError("Поле «topic_number» должно быть строкой или null.")
        if parent_section is not None and not isinstance(parent_section, str):
            raise GenerationContractError(
                "Поле «parent_section» должно быть строкой или null."
            )
        encoded.append(
            {
                "topic_number": number,
                "topic_title": str(title),
                "parent_section": parent_section,
                "review": dict(value),
            }
        )
    return encoded


def decode_match_reviews(value: object) -> dict[tuple[str | None, str, str | None], dict[str, Any]]:
    """Restore apply_match_reviews keys; keep a legacy mapping as-is."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, list):
        raise GenerationContractError(
            "Поле «match_reviews» должно быть массивом записей или объектом."
        )
    if len(value) > MAX_MATCH_REVIEW_RECORDS:
        raise GenerationContractError("Слишком много match_reviews.")
    restored: dict[tuple[str | None, str, str | None], dict[str, Any]] = {}
    for raw in value:
        record = _required_mapping(raw, field="match_reviews[]")
        title = _required_text(record.get("topic_title"), field="topic_title")
        key = (
            _optional_identity_text(record.get("topic_number"), field="topic_number"),
            title,
            _optional_identity_text(
                record.get("parent_section"), field="parent_section"
            ),
        )
        if key in restored:
            raise GenerationContractError("Повторяющийся ключ match_reviews.")
        review = record.get("review")
        if not isinstance(review, Mapping):
            raise GenerationContractError("Поле «review» должно быть объектом.")
        item_ref = review.get("item_ref")
        if item_ref is not None and not isinstance(item_ref, Mapping):
            raise GenerationContractError("Поле «item_ref» должно быть объектом или null.")
        restored[key] = dict(review)
    return restored
