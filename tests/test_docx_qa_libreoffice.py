import json
import logging
from pathlib import Path
import subprocess

import pymupdf
import pytest

import calendar_pedagoga.docx_qa as docx_qa
from calendar_pedagoga.docx_qa import _run_soffice, render_docx_pages


def _pdf_bytes(page_count: int) -> bytes:
    document = pymupdf.open()
    try:
        for page_number in range(1, page_count + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"QA page {page_number}")
        return document.tobytes()
    finally:
        document.close()


def _force_libreoffice_path(
    monkeypatch: pytest.MonkeyPatch,
    qa_temp: Path,
) -> None:
    qa_temp.mkdir()
    monkeypatch.setattr(docx_qa, "_docx_to_pdf_bytes_word", lambda _: None)
    monkeypatch.setattr(docx_qa, "find_libreoffice", lambda: Path("/usr/bin/soffice"))
    monkeypatch.setattr(docx_qa.tempfile, "mkdtemp", lambda **_: str(qa_temp))


def test_soffice_uses_a_unique_profile_for_every_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _run_soffice(Path("soffice.exe"), ["--convert-to", "pdf"], tmp_path)
    _run_soffice(Path("soffice.exe"), ["--convert-to", "pdf"], tmp_path)

    profiles = [
        next(argument for argument in command if argument.startswith("-env:UserInstallation="))
        for command in commands
    ]
    assert len(set(profiles)) == 2
    assert all(profile.startswith("-env:UserInstallation=file:") for profile in profiles)


def test_soffice_failure_logs_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "LibreOffice 7.6", "")
        return subprocess.CompletedProcess(command, 17, "conversion output", "conversion error")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR, logger=docx_qa.__name__):
        with pytest.raises(RuntimeError, match="diagnostics logged"):
            _run_soffice(Path("soffice.exe"), ["--convert-to", "pdf"], tmp_path)

    record = next(
        record for record in caplog.records if "LibreOffice visual QA failure" in record.message
    )
    diagnostics = json.loads(record.message.split(": ", 1)[1])
    assert diagnostics["soffice_executable"] == "soffice.exe"
    assert diagnostics["libreoffice_version"] == "LibreOffice 7.6"
    assert diagnostics["return_code"] == 17
    assert diagnostics["stdout"] == "conversion output"
    assert diagnostics["stderr"] == "conversion error"
    assert diagnostics["command"][0] == "soffice.exe"
    assert diagnostics["pdf_exists"] is False
    assert diagnostics["pdf_size"] is None
    assert diagnostics["pdf_page_count"] is None
    assert diagnostics["pymupdf_exception_type"] is None
    assert not (tmp_path / "libreoffice_failure.json").exists()


def test_libreoffice_converts_only_docx_to_pdf_and_pymupdf_renders_all_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qa_temp = tmp_path / "qa_temp"
    output_dir = tmp_path / "rendered"
    _force_libreoffice_path(monkeypatch, qa_temp)
    calls: list[list[str]] = []

    def fake_soffice(
        soffice: Path,
        arguments: list[str],
        _: Path,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        output = Path(arguments[arguments.index("--outdir") + 1]) / "calendar.pdf"
        output.write_bytes(_pdf_bytes(2))
        command = [str(soffice), "--headless", *arguments]
        return subprocess.CompletedProcess(command, 0, "converted", "")

    monkeypatch.setattr(docx_qa, "_run_soffice", fake_soffice)

    rendered = render_docx_pages(b"private document bytes", output_dir)

    assert len(calls) == 1
    assert calls[0][calls[0].index("--convert-to") + 1] == "pdf"
    assert "png" not in calls[0]
    assert [path.name for path in rendered] == ["page_01.png", "page_02.png"]
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in rendered)
    assert not qa_temp.exists()


def test_request_cache_reuses_only_byte_identical_docx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []
    monkeypatch.setattr(docx_qa, "find_libreoffice", lambda: Path("/usr/bin/soffice"))

    def fake_soffice(
        soffice: Path,
        arguments: list[str],
        temp_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        source = Path(arguments[-1]).read_bytes()
        calls.append(source)
        output = Path(arguments[arguments.index("--outdir") + 1]) / "calendar.pdf"
        output.write_bytes(b"pdf:" + source)
        return subprocess.CompletedProcess([str(soffice), *arguments], 0, "", "")

    monkeypatch.setattr(docx_qa, "_run_soffice", fake_soffice)

    with docx_qa.libreoffice_pdf_render_cache():
        first = docx_qa._docx_to_pdf_bytes_libreoffice(b"same docx")
        repeated = docx_qa._docx_to_pdf_bytes_libreoffice(b"same docx")
        changed = docx_qa._docx_to_pdf_bytes_libreoffice(b"changed docx")

    assert first == repeated == b"pdf:same docx"
    assert changed == b"pdf:changed docx"
    assert calls == [b"same docx", b"changed docx"]


def test_render_cache_is_request_scoped_and_keys_layout_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []
    soffice = Path("/usr/bin/soffice")
    monkeypatch.setattr(docx_qa, "find_libreoffice", lambda: soffice)

    def fake_soffice(
        executable: Path,
        arguments: list[str],
        temp_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        source = Path(arguments[-1]).read_bytes()
        calls.append(source)
        output = Path(arguments[arguments.index("--outdir") + 1]) / "calendar.pdf"
        output.write_bytes(b"pdf")
        return subprocess.CompletedProcess([str(executable), *arguments], 0, "", "")

    monkeypatch.setattr(docx_qa, "_run_soffice", fake_soffice)

    with docx_qa.libreoffice_pdf_render_cache():
        docx_qa._docx_to_pdf_bytes_libreoffice(b"docx")
    with docx_qa.libreoffice_pdf_render_cache():
        docx_qa._docx_to_pdf_bytes_libreoffice(b"docx")

    assert calls == [b"docx", b"docx"]
    default = docx_qa._libreoffice_pdf_cache_key(b"docx", soffice)
    split = docx_qa._libreoffice_pdf_cache_key(
        b"docx", soffice, layout_options=("allow-split:1",)
    )
    assert default != split


def test_visual_qa_reuses_cached_final_layout_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[bytes] = []
    pdf = _pdf_bytes(2)
    monkeypatch.setattr(docx_qa, "_docx_to_pdf_bytes_word", lambda _: None)
    monkeypatch.setattr(docx_qa, "find_libreoffice", lambda: Path("/usr/bin/soffice"))

    def fake_soffice(
        soffice: Path,
        arguments: list[str],
        temp_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        source = Path(arguments[-1]).read_bytes()
        calls.append(source)
        output = Path(arguments[arguments.index("--outdir") + 1]) / "calendar.pdf"
        output.write_bytes(pdf)
        return subprocess.CompletedProcess([str(soffice), *arguments], 0, "", "")

    monkeypatch.setattr(docx_qa, "_run_soffice", fake_soffice)
    monkeypatch.setattr(
        docx_qa,
        "_data_row_page_spans_pdf",
        lambda content, rendered_pdf, total_rows: (
            docx_qa.DataRowPageSpan(1, 1, True),
        ),
    )

    final_docx = b"byte-exact final layout"
    with docx_qa.libreoffice_pdf_render_cache():
        assert docx_qa.detect_data_row_page_spans(
            final_docx,
            total_rows=1,
            render_exact=True,
        ) == (docx_qa.DataRowPageSpan(1, 1, True),)
        rendered = render_docx_pages(final_docx, tmp_path / "rendered")

    assert calls == [final_docx]
    assert [path.name for path in rendered] == ["page_01.png", "page_02.png"]


def test_pymupdf_failure_is_logged_and_temp_directory_is_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    qa_temp = tmp_path / "qa_temp"
    output_dir = tmp_path / "rendered"
    _force_libreoffice_path(monkeypatch, qa_temp)
    monkeypatch.setattr(docx_qa, "_libreoffice_version", lambda _: "LibreOffice test")

    def fake_soffice(
        soffice: Path,
        arguments: list[str],
        _: Path,
    ) -> subprocess.CompletedProcess[str]:
        output = Path(arguments[arguments.index("--outdir") + 1]) / "calendar.pdf"
        output.write_bytes(b"not a PDF")
        command = [str(soffice), "--headless", *arguments]
        return subprocess.CompletedProcess(command, 0, "converted", "")

    monkeypatch.setattr(docx_qa, "_run_soffice", fake_soffice)

    with caplog.at_level(logging.ERROR, logger=docx_qa.__name__):
        with pytest.raises(RuntimeError, match="PyMuPDF.*diagnostics logged"):
            render_docx_pages(b"private document bytes", output_dir)

    record = next(
        record for record in caplog.records if "LibreOffice visual QA failure" in record.message
    )
    diagnostics = json.loads(record.message.split(": ", 1)[1])
    assert diagnostics["libreoffice_version"] == "LibreOffice test"
    assert diagnostics["return_code"] == 0
    assert diagnostics["stdout"] == "converted"
    assert diagnostics["pdf_exists"] is True
    assert diagnostics["pdf_size"] == len(b"not a PDF")
    assert diagnostics["pdf_page_count"] is None
    assert diagnostics["pymupdf_exception_type"]
    assert diagnostics["pymupdf_exception_message"]
    assert "private document bytes" not in caplog.text
    assert not qa_temp.exists()


def test_libreoffice_failure_removes_temp_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qa_temp = tmp_path / "qa_temp"
    output_dir = tmp_path / "rendered"
    _force_libreoffice_path(monkeypatch, qa_temp)

    def fail_soffice(*_: object) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("LibreOffice convert failed; diagnostics logged.")

    monkeypatch.setattr(docx_qa, "_run_soffice", fail_soffice)

    with pytest.raises(RuntimeError, match="LibreOffice convert failed"):
        render_docx_pages(b"private document bytes", output_dir)

    assert not qa_temp.exists()


def test_word_pdf_path_does_not_call_libreoffice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(docx_qa, "_docx_to_pdf_bytes_word", lambda _: _pdf_bytes(1))

    def unexpected_libreoffice() -> Path:
        raise AssertionError("LibreOffice path must not be used when Word produced PDF")

    monkeypatch.setattr(docx_qa, "find_libreoffice", unexpected_libreoffice)

    rendered = render_docx_pages(b"docx", tmp_path / "rendered")

    assert [path.name for path in rendered] == ["page_01.png"]
