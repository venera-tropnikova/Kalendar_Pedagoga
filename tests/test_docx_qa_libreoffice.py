import json
from pathlib import Path
import subprocess

import pytest

from calendar_pedagoga.docx_qa import _run_soffice


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
    _run_soffice(Path("soffice.exe"), ["--convert-to", "png"], tmp_path)

    profiles = [
        next(argument for argument in command if argument.startswith("-env:UserInstallation="))
        for command in commands
    ]
    assert len(set(profiles)) == 2
    assert all(profile.startswith("-env:UserInstallation=file:") for profile in profiles)


def test_soffice_failure_preserves_diagnostics_and_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "calendar.docx"
    source.write_bytes(b"docx")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 17, "conversion output", "conversion error")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="diagnostics and temp files"):
        _run_soffice(Path("soffice.exe"), ["--convert-to", "pdf"], tmp_path)

    diagnostics_path = tmp_path / "libreoffice_failure.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["return_code"] == 17
    assert diagnostics["stdout"] == "conversion output"
    assert diagnostics["stderr"] == "conversion error"
    assert diagnostics["temp_directory"] == str(tmp_path)
    assert diagnostics["command"][0] == "soffice.exe"
    assert source.is_file()
