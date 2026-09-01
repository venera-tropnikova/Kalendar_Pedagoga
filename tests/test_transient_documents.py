from dataclasses import fields
from pathlib import Path

import pytest

from calendar_pedagoga.transient_documents import (
    OperationMetadata,
    TransientDocumentSession,
    temporary_document_file,
)
from calendar_pedagoga.upload_validation import UploadPurpose


class OperationCancelled(Exception):
    pass


def test_success_clears_memory_and_temporary_file() -> None:
    session = TransientDocumentSession()
    temporary_path: Path | None = None

    with session:
        session.replace(UploadPurpose.UTP, "plan.docx", b"source")
        with temporary_document_file(b"technical", ".tmp") as path:
            temporary_path = path
            assert path.is_file()

    assert session.document_count == 0
    assert session.total_bytes == 0
    assert temporary_path is not None
    assert not temporary_path.exists()
    assert not temporary_path.parent.exists()


def test_error_clears_memory_and_temporary_file() -> None:
    session = TransientDocumentSession()
    temporary_path: Path | None = None

    with pytest.raises(RuntimeError, match="processing failed"):
        with session:
            session.replace(UploadPurpose.PROGRAM, "program.docx", b"source")
            with temporary_document_file(b"technical", ".tmp") as path:
                temporary_path = path
                raise RuntimeError("processing failed")

    assert session.document_count == 0
    assert temporary_path is not None
    assert not temporary_path.exists()
    assert not temporary_path.parent.exists()


def test_cancellation_clears_memory_and_temporary_file() -> None:
    session = TransientDocumentSession()
    temporary_path: Path | None = None

    with pytest.raises(OperationCancelled):
        with session:
            session.replace(
                UploadPurpose.CALENDAR_TEMPLATE, "template.docx", b"source"
            )
            with temporary_document_file(b"technical", ".tmp") as path:
                temporary_path = path
                raise OperationCancelled

    assert session.document_count == 0
    assert temporary_path is not None
    assert not temporary_path.exists()
    assert not temporary_path.parent.exists()


def test_reupload_replaces_previous_file_without_copies() -> None:
    session = TransientDocumentSession()
    session.replace(UploadPurpose.UTP, "old.docx", b"old")
    session.replace(UploadPurpose.UTP, "new.docx", b"new")

    current = session.get(UploadPurpose.UTP)
    assert session.document_count == 1
    assert current is not None
    assert current.filename == "new.docx"
    assert current.content == b"new"


def test_new_session_cannot_see_previous_session_documents() -> None:
    previous = TransientDocumentSession()
    previous.replace(UploadPurpose.PROGRAM, "program.docx", b"source")
    previous.clear()

    new_session = TransientDocumentSession()

    assert new_session.document_count == 0
    assert new_session.get(UploadPurpose.PROGRAM) is None


def test_operation_metadata_cannot_contain_document_content() -> None:
    assert {field.name for field in fields(OperationMetadata)} == {
        "registry_version",
        "operated_at",
        "status",
        "file_count",
        "total_bytes",
    }


def test_result_is_removed_immediately_after_download_transfer() -> None:
    session = TransientDocumentSession()
    session.publish_result("calendar.docx", b"generated-result")

    download = session.take_result_for_download()

    assert download is not None
    assert download.filename == "calendar.docx"
    assert download.content == b"generated-result"
    assert not session.has_result
    assert session.take_result_for_download() is None


def test_unclaimed_result_is_removed_when_operation_ends() -> None:
    session = TransientDocumentSession()

    with session:
        session.publish_result("calendar.docx", b"generated-result")
        assert session.has_result

    assert not session.has_result


def test_new_result_replaces_previous_without_history() -> None:
    session = TransientDocumentSession()
    session.publish_result("old.docx", b"old-result")
    session.publish_result("new.docx", b"new-result")

    download = session.take_result_for_download()

    assert download is not None
    assert download.filename == "new.docx"
    assert download.content == b"new-result"
    assert not session.has_result


def test_new_session_cannot_see_previous_result() -> None:
    previous = TransientDocumentSession()
    previous.publish_result("calendar.docx", b"generated-result")
    previous.clear()

    new_session = TransientDocumentSession()

    assert not new_session.has_result
    assert new_session.take_result_for_download() is None