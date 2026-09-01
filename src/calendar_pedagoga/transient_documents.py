"""Временный жизненный цикл пользовательских документов без постоянного хранения."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
import tempfile
from typing import Iterator

from calendar_pedagoga.upload_validation import UploadPurpose


@dataclass(frozen=True)
class TransientDocument:
    """Пользовательский документ, существующий только в памяти текущей операции."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class TransientResult:
    """Готовый файл в памяти до однократной передачи пользователю."""

    filename: str
    content: bytes


class OperationStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OperationMetadata:
    """Разрешённые технические метаданные без содержимого документов."""

    registry_version: str
    operated_at: datetime
    status: OperationStatus
    file_count: int
    total_bytes: int


class TransientDocumentSession:
    """Изолированный контейнер операции: один актуальный файл каждого типа."""

    def __init__(self) -> None:
        self._documents: dict[UploadPurpose, TransientDocument] = {}
        self._result: TransientResult | None = None

    def __enter__(self) -> TransientDocumentSession:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.clear()

    def replace(self, purpose: UploadPurpose, filename: str, content: bytes) -> None:
        """Заменить предыдущий файл этого типа, не создавая копий на диске."""

        self._documents[purpose] = TransientDocument(Path(filename).name, content)

    def get(self, purpose: UploadPurpose) -> TransientDocument | None:
        return self._documents.get(purpose)

    def publish_result(self, filename: str, content: bytes) -> None:
        """Заменить результат операции, не записывая его на диск."""

        if not content:
            raise ValueError("Готовый файл не может быть пустым.")
        self._result = TransientResult(Path(filename).name, content)

    def take_result_for_download(self) -> TransientResult | None:
        """Однократно передать результат и немедленно удалить его из сессии."""

        result = self._result
        self._result = None
        return result

    def clear(self) -> None:
        self._documents.clear()
        self._result = None

    @property
    def has_result(self) -> bool:
        return self._result is not None

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def total_bytes(self) -> int:
        return sum(len(document.content) for document in self._documents.values())


@contextmanager
def temporary_document_file(
    content: bytes,
    suffix: str,
) -> Iterator[Path]:
    """Создать технически необходимый файл и удалить каталог при любом исходе."""

    with tempfile.TemporaryDirectory(prefix="calendar_pedagoga_") as temp_name:
        path = Path(temp_name) / f"document{suffix}"
        path.write_bytes(content)
        yield path
