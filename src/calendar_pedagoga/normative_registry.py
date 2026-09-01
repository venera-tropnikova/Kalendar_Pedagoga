"""Версионируемый реестр нормативных документов приложения."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import urlparse


class NormativeDocumentStatus(StrEnum):
    """Состояние нормативного документа на дату версии реестра."""

    ACTIVE = "active"
    AMENDED = "amended"
    REPEALED = "repealed"


@dataclass(frozen=True)
class NormativeDocumentChange:
    """Одно проверенное изменение нормативной записи."""

    changed_on: date
    description: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Описание изменения не может быть пустым.")


@dataclass(frozen=True)
class NormativeDocument:
    """Состояние документа внутри конкретной версии реестра."""

    title: str
    number: str
    document_date: date
    status: NormativeDocumentStatus
    regulates: str
    official_url: str
    effective_from: date | None = None
    effective_until: date | None = None
    verified_on: date | None = None
    replaced_by: str | None = None
    change_history: tuple[NormativeDocumentChange, ...] = ()

    def __post_init__(self) -> None:
        text_fields = {
            "title": self.title,
            "number": self.number,
            "regulates": self.regulates,
            "official_url": self.official_url,
        }
        if any(not value.strip() for value in text_fields.values()):
            raise ValueError("Поля нормативного документа не могут быть пустыми.")
        parsed_url = urlparse(self.official_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("official_url должен быть абсолютным HTTPS URL.")
        if self.effective_until is not None and self.effective_from is None:
            raise ValueError("Для effective_until требуется effective_from.")
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until не может быть раньше effective_from.")
        if self.verified_on is not None and self.verified_on < self.document_date:
            raise ValueError("verified_on не может быть раньше даты документа.")
        if self.replaced_by is not None and not self.replaced_by.strip():
            raise ValueError("replaced_by не может быть пустой строкой.")
        if any(change.changed_on < self.document_date for change in self.change_history):
            raise ValueError("Изменение не может предшествовать дате документа.")
        if tuple(sorted(self.change_history, key=lambda item: item.changed_on)) != self.change_history:
            raise ValueError("История изменений должна быть упорядочена по дате.")


@dataclass(frozen=True)
class NormativeRegistrySnapshot:
    """Неизменяемый снимок нормативной базы."""

    registry_version: str
    effective_from: date
    verified_on: date
    documents: tuple[NormativeDocument, ...] = ()

    def __post_init__(self) -> None:
        if not self.registry_version.strip():
            raise ValueError("registry_version не может быть пустой.")
        if self.verified_on < self.effective_from:
            raise ValueError("verified_on не может быть раньше effective_from.")


@dataclass(frozen=True)
class NormativeRegistry:
    """История снимков; прежние версии сохраняются без изменения."""

    versions: tuple[NormativeRegistrySnapshot, ...]

    def __post_init__(self) -> None:
        if not self.versions:
            raise ValueError("Реестр должен содержать хотя бы одну версию.")
        version_ids = [snapshot.registry_version for snapshot in self.versions]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("Версии реестра должны быть уникальными.")
        effective_dates = [snapshot.effective_from for snapshot in self.versions]
        if effective_dates != sorted(effective_dates):
            raise ValueError("Версии реестра должны идти по effective_from.")

    @property
    def current(self) -> NormativeRegistrySnapshot:
        return self.versions[-1]

    def get_version(self, registry_version: str) -> NormativeRegistrySnapshot:
        for snapshot in self.versions:
            if snapshot.registry_version == registry_version:
                return snapshot
        raise KeyError(f"Версия реестра не найдена: {registry_version}")

    def with_version(self, snapshot: NormativeRegistrySnapshot) -> NormativeRegistry:
        """Вернуть новый реестр, сохранив все прежние снимки."""

        return NormativeRegistry(self.versions + (snapshot,))


class NormativeUpdateChoice(StrEnum):
    APPLY_CURRENT = "apply_current"
    KEEP_EXISTING = "keep_existing"


@dataclass(frozen=True)
class NormativeUpdateNotice:
    calendar_version: str
    available_version: str

    @property
    def update_available(self) -> bool:
        return self.calendar_version != self.available_version


def get_update_notice(
    calendar_reference: CalendarRegistryReference,
    registry: NormativeRegistry,
) -> NormativeUpdateNotice:
    """Только сообщить о доступной версии, ничего не изменяя."""

    return NormativeUpdateNotice(
        calendar_version=calendar_reference.registry_version,
        available_version=registry.current.registry_version,
    )


def resolve_registry_reference(
    calendar_reference: CalendarRegistryReference,
    registry: NormativeRegistry,
    choice: NormativeUpdateChoice,
) -> CalendarRegistryReference:
    """Изменить ссылку только после явного выбора пользователя."""

    if choice is NormativeUpdateChoice.APPLY_CURRENT:
        return bind_calendar_to_registry(registry.current)
    return calendar_reference


@dataclass(frozen=True)
class CalendarRegistryReference:
    """Версия нормативной базы, сохранённая будущим календарём."""

    registry_version: str


def bind_calendar_to_registry(
    snapshot: NormativeRegistrySnapshot,
) -> CalendarRegistryReference:
    return CalendarRegistryReference(registry_version=snapshot.registry_version)


BUILTIN_NORMATIVE_REGISTRY = NormativeRegistry(
    versions=(
        NormativeRegistrySnapshot(
            registry_version="0.1.0",
            effective_from=date(2026, 9, 1),
            verified_on=date(2026, 9, 1),
            documents=(
                NormativeDocument(
                    title="Об образовании в Российской Федерации",
                    number="273-ФЗ",
                    document_date=date(2012, 12, 29),
                    status=NormativeDocumentStatus.ACTIVE,
                    regulates=(
                        "Правовые, организационные и экономические основы "
                        "образования в Российской Федерации."
                    ),
                    official_url=(
                        "https://publication.pravo.gov.ru/document/"
                        "0001201212300007"
                    ),
                    verified_on=date(2026, 9, 1),
                    change_history=(
                        NormativeDocumentChange(
                            changed_on=date(2026, 9, 1),
                            description="Добавлен в нормативный реестр 0.1.0.",
                        ),
                    ),
                ),
                NormativeDocument(
                    title=(
                        "Об утверждении Порядка организации и осуществления "
                        "образовательной деятельности по дополнительным "
                        "общеобразовательным программам"
                    ),
                    number="629",
                    document_date=date(2022, 7, 27),
                    status=NormativeDocumentStatus.ACTIVE,
                    regulates=(
                        "Порядок организации и осуществления образовательной "
                        "деятельности по дополнительным общеобразовательным программам."
                    ),
                    official_url=(
                        "https://publication.pravo.gov.ru/document/"
                        "0001202209270013"
                    ),
                    effective_from=date(2023, 3, 1),
                    effective_until=date(2029, 2, 28),
                    verified_on=date(2026, 9, 1),
                    change_history=(
                        NormativeDocumentChange(
                            changed_on=date(2026, 9, 1),
                            description="Добавлен в нормативный реестр 0.1.0.",
                        ),
                    ),
                ),
                NormativeDocument(
                    title=(
                        "Об утверждении СП 2.4.2.4283-26 "
                        "«Санитарно-эпидемиологические требования к организациям "
                        "воспитания и обучения, отдыха и оздоровления детей и молодежи»"
                    ),
                    number="19 / СП 2.4.2.4283-26",
                    document_date=date(2026, 6, 2),
                    status=NormativeDocumentStatus.ACTIVE,
                    regulates=(
                        "Санитарно-эпидемиологические требования к организациям "
                        "воспитания и обучения, отдыха и оздоровления детей и молодежи."
                    ),
                    official_url=(
                        "https://publication.pravo.gov.ru/document/"
                        "0001202606020083"
                    ),
                    effective_from=date(2026, 9, 1),
                    effective_until=date(2032, 9, 1),
                    verified_on=date(2026, 9, 1),
                    change_history=(
                        NormativeDocumentChange(
                            changed_on=date(2026, 9, 1),
                            description="Добавлен в нормативный реестр 0.1.0.",
                        ),
                    ),
                ),
            ),
        ),
    )
)


def get_builtin_normative_registry() -> NormativeRegistry:
    return BUILTIN_NORMATIVE_REGISTRY


def get_builtin_normative_documents() -> tuple[NormativeDocument, ...]:
    """Совместимый доступ к документам текущей версии."""

    return BUILTIN_NORMATIVE_REGISTRY.current.documents
