from dataclasses import FrozenInstanceError, fields
from datetime import date

import pytest

from calendar_pedagoga.ai_preparation import INSTRUCTIONS
from calendar_pedagoga.normative_registry import (
    BUILTIN_NORMATIVE_REGISTRY,
    CalendarRegistryReference,
    NormativeDocument,
    NormativeDocumentChange,
    NormativeDocumentStatus,
    NormativeUpdateChoice,
    NormativeRegistry,
    NormativeRegistrySnapshot,
    bind_calendar_to_registry,
    get_builtin_normative_documents,
    get_builtin_normative_registry,
    get_update_notice,
    resolve_registry_reference,
)


def _document(*, status: NormativeDocumentStatus = NormativeDocumentStatus.ACTIVE) -> NormativeDocument:
    return NormativeDocument(
        title="Тестовый нормативный документ",
        number="TEST-001",
        document_date=date(2025, 1, 1),
        status=status,
        regulates="Проверяет структуру записи реестра.",
        official_url="https://example.test/document",
        replaced_by="TEST-002" if status is NormativeDocumentStatus.REPEALED else None,
        change_history=(
            NormativeDocumentChange(
                changed_on=date(2025, 2, 1),
                description="Тестовое изменение записи.",
            ),
        ),
    )


def test_normative_document_schema_has_required_fields() -> None:
    assert {field.name for field in fields(NormativeDocument)} == {
        "title",
        "number",
        "document_date",
        "status",
        "regulates",
        "official_url",
        "effective_from",
        "effective_until",
        "verified_on",
        "replaced_by",
        "change_history",
    }


def test_registry_snapshot_has_version_metadata() -> None:
    assert {field.name for field in fields(NormativeRegistrySnapshot)} == {
        "registry_version",
        "effective_from",
        "verified_on",
        "documents",
    }


def test_document_status_contract_is_exact() -> None:
    assert {status.value for status in NormativeDocumentStatus} == {
        "active",
        "amended",
        "repealed",
    }


def test_builtin_registry_0_1_0_contains_three_verified_documents() -> None:
    registry = get_builtin_normative_registry()
    documents = get_builtin_normative_documents()
    assert registry is BUILTIN_NORMATIVE_REGISTRY
    assert registry.current.registry_version == "0.1.0"
    assert registry.current.verified_on == date(2026, 9, 1)
    assert len(documents) == 3
    assert all(document.status is NormativeDocumentStatus.ACTIVE for document in documents)
    assert all(document.verified_on == date(2026, 9, 1) for document in documents)
    assert all(document.official_url.startswith("https://publication.pravo.gov.ru/document/") for document in documents)


def test_builtin_registry_0_1_0_has_exact_document_identifiers_and_terms() -> None:
    documents = {document.number: document for document in get_builtin_normative_documents()}
    assert set(documents) == {"273-ФЗ", "629", "19 / СП 2.4.2.4283-26"}
    assert documents["273-ФЗ"].document_date == date(2012, 12, 29)
    assert documents["273-ФЗ"].effective_until is None
    assert documents["629"].document_date == date(2022, 7, 27)
    assert documents["629"].effective_from == date(2023, 3, 1)
    assert documents["629"].effective_until == date(2029, 2, 28)
    sanitary = documents["19 / СП 2.4.2.4283-26"]
    assert sanitary.document_date == date(2026, 6, 2)
    assert sanitary.effective_from == date(2026, 9, 1)
    assert sanitary.effective_until == date(2032, 9, 1)


def test_builtin_registry_contains_no_extra_mandatory_materials() -> None:
    registry_text = " ".join(
        document.title + " " + document.regulates
        for document in get_builtin_normative_documents()
    ).casefold()
    assert "календарный план воспитательной работы" not in registry_text
    assert "методические рекомендации" not in registry_text


def test_document_keeps_replacement_and_change_history() -> None:
    document = _document(status=NormativeDocumentStatus.REPEALED)
    assert document.replaced_by == "TEST-002"
    assert document.change_history[0].description == "Тестовое изменение записи."


def test_registry_update_keeps_previous_snapshot() -> None:
    first = NormativeRegistrySnapshot(
        registry_version="1.0.0",
        effective_from=date(2025, 1, 1),
        verified_on=date(2025, 1, 2),
    )
    registry = NormativeRegistry((first,))
    second = NormativeRegistrySnapshot(
        registry_version="1.1.0",
        effective_from=date(2025, 3, 1),
        verified_on=date(2025, 3, 2),
        documents=(_document(status=NormativeDocumentStatus.AMENDED),),
    )

    updated = registry.with_version(second)

    assert registry.versions == (first,)
    assert updated.versions == (first, second)
    assert updated.get_version("1.0.0") is first
    assert updated.current is second


def test_calendar_reference_does_not_change_after_registry_update() -> None:
    first = NormativeRegistrySnapshot(
        registry_version="1.0.0",
        effective_from=date(2025, 1, 1),
        verified_on=date(2025, 1, 2),
    )
    registry = NormativeRegistry((first,))
    calendar_reference = bind_calendar_to_registry(registry.current)
    updated = registry.with_version(
        NormativeRegistrySnapshot(
            registry_version="2.0.0",
            effective_from=date(2026, 1, 1),
            verified_on=date(2026, 1, 2),
        )
    )

    assert calendar_reference == CalendarRegistryReference("1.0.0")
    assert updated.current.registry_version == "2.0.0"
    assert calendar_reference.registry_version == "1.0.0"


def test_snapshots_are_immutable() -> None:
    snapshot = NormativeRegistrySnapshot(
        registry_version="1.0.0",
        effective_from=date(2025, 1, 1),
        verified_on=date(2025, 1, 2),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.registry_version = "changed"  # type: ignore[misc]


def test_duplicate_or_out_of_order_versions_are_rejected() -> None:
    first = NormativeRegistrySnapshot("1.0.0", date(2025, 2, 1), date(2025, 2, 1))
    duplicate = NormativeRegistrySnapshot("1.0.0", date(2025, 3, 1), date(2025, 3, 1))
    earlier = NormativeRegistrySnapshot("0.9.0", date(2025, 1, 1), date(2025, 1, 1))
    with pytest.raises(ValueError, match="уникальными"):
        NormativeRegistry((first, duplicate))
    with pytest.raises(ValueError, match="effective_from"):
        NormativeRegistry((first, earlier))


@pytest.mark.parametrize("url", ["", "http://example.test/document", "relative/path"])
def test_registry_rejects_non_official_url_shape(url: str) -> None:
    with pytest.raises(ValueError):
        NormativeDocument(
            title="Тестовый документ",
            number="TEST-002",
            document_date=date(2025, 1, 1),
            status=NormativeDocumentStatus.ACTIVE,
            regulates="Тест.",
            official_url=url,
        )


def test_normative_registry_is_not_embedded_in_ai_prompt() -> None:
    prompt = " ".join(INSTRUCTIONS).casefold()
    assert "норматив" not in prompt
    assert "registry_version" not in prompt
    assert "official_url" not in prompt
    assert "verified_on" not in prompt


def test_normative_update_only_notifies_until_user_chooses() -> None:
    first = NormativeRegistrySnapshot("1.0.0", date(2025, 1, 1), date(2025, 1, 1))
    second = NormativeRegistrySnapshot("2.0.0", date(2026, 1, 1), date(2026, 1, 1))
    registry = NormativeRegistry((first, second))
    existing = CalendarRegistryReference("1.0.0")

    notice = get_update_notice(existing, registry)

    assert notice.update_available
    assert notice.calendar_version == "1.0.0"
    assert notice.available_version == "2.0.0"
    assert existing.registry_version == "1.0.0"


def test_normative_version_changes_only_after_explicit_choice() -> None:
    first = NormativeRegistrySnapshot("1.0.0", date(2025, 1, 1), date(2025, 1, 1))
    second = NormativeRegistrySnapshot("2.0.0", date(2026, 1, 1), date(2026, 1, 1))
    registry = NormativeRegistry((first, second))
    existing = CalendarRegistryReference("1.0.0")

    kept = resolve_registry_reference(
        existing, registry, NormativeUpdateChoice.KEEP_EXISTING
    )
    applied = resolve_registry_reference(
        existing, registry, NormativeUpdateChoice.APPLY_CURRENT
    )

    assert kept is existing
    assert applied.registry_version == "2.0.0"
    assert existing.registry_version == "1.0.0"