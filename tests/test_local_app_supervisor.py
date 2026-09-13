"""Tests for local Streamlit supervisor and shared generator revision."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

from calendar_pedagoga.generator_revision import generator_paths, generator_revision
from calendar_pedagoga.local_app_supervisor import (
    DEFAULT_PORT,
    LocalAppSupervisor,
    SupervisorConfig,
    build_streamlit_command,
    main,
)


ROOT = Path(__file__).resolve().parents[1]


def _make_mini_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "src" / "calendar_pedagoga"
    pkg.mkdir(parents=True)
    (root / "app.py").write_text("print('app')\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
    refs = root / "references"
    refs.mkdir()
    # Minimal zip-like bytes are fine; revision only hashes file bytes.
    (refs / "Календарный план Образец.docx").write_bytes(b"PK\x03\x04fake-docx")
    return root


def test_generator_revision_changes_when_generator_file_changes(tmp_path: Path):
    root = _make_mini_project(tmp_path)
    before = generator_revision(root)
    engine = root / "src" / "calendar_pedagoga" / "engine.py"
    engine.write_text("VALUE = 2\n", encoding="utf-8")
    after = generator_revision(root)
    assert before != after
    assert engine in generator_paths(root)


def test_build_streamlit_command_uses_fixed_port():
    command = build_streamlit_command("python", ROOT, DEFAULT_PORT)
    assert command[:4] == ["python", "-m", "streamlit", "run"]
    assert "--server.port" in command
    assert command[command.index("--server.port") + 1] == "8501"


def test_main_rejects_non_8501_port():
    assert main(["--port", "8502"]) == 2


def test_supervisor_restarts_on_generator_change(tmp_path: Path):
    root = _make_mini_project(tmp_path)
    started: list[tuple[str, str]] = []
    stopped: list[list[int]] = []
    revisions = {"value": generator_revision(root)}

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid
            self._code = None

        def poll(self):
            return self._code

    processes = {"n": 0}

    def fake_popen(command, **_kwargs):
        processes["n"] += 1
        rev = revisions["value"]
        started.append((rev, " ".join(command)))
        return FakeProcess(pid=1000 + processes["n"])

    def fake_stop(_port: int) -> list[int]:
        pid = 1000 + processes["n"]
        stopped.append([pid])
        return [pid]

    def revision_fn(_root: Path) -> str:
        return revisions["value"]

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(started) == 1 and len(sleeps) >= 2:
            # Simulate a generator edit after the first child is up.
            engine = root / "src" / "calendar_pedagoga" / "engine.py"
            engine.write_text("VALUE = 99\n", encoding="utf-8")
            revisions["value"] = generator_revision(root)
        if len(started) >= 2:
            supervisor.request_stop()

    config = SupervisorConfig(
        root=root,
        port=DEFAULT_PORT,
        poll_seconds=0.01,
        debounce_seconds=0.02,
        ready_timeout_seconds=0.1,
        python_executable="python",
    )
    supervisor = LocalAppSupervisor(
        config,
        revision_fn=revision_fn,
        popen=fake_popen,
        stop_listeners=fake_stop,
        wait_ready=lambda _port, _timeout: True,
        sleep=fake_sleep,
    )
    supervisor.run_forever(max_iterations=50)

    assert len(started) >= 2
    assert started[0][0] != started[1][0]
    assert all("streamlit" in cmd for _, cmd in started)
    assert stopped, "old process must be terminated before restart"
    assert supervisor.managed.revision == started[-1][0]


def test_auto_restart_with_real_child_process(tmp_path: Path):
    """start → change generator file → automatic restart → new revision loaded."""
    root = _make_mini_project(tmp_path)
    marker = root / "child_log.txt"
    child_script = root / "fake_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import os, socket, sys, time",
                "from pathlib import Path",
                "rev = os.environ['APP_REVISION_EXPECT']",
                "port = int(os.environ['APP_PORT'])",
                "Path(os.environ['CHILD_LOG']).write_text(",
                "    f'APP_REVISION={rev}\\nPID={os.getpid()}\\n', encoding='utf-8')",
                "sock = socket.socket()",
                "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
                "sock.bind(('127.0.0.1', port))",
                "sock.listen(1)",
                "deadline = time.time() + 20",
                "while time.time() < deadline:",
                "    time.sleep(0.2)",
                "sock.close()",
            ]
        ),
        encoding="utf-8",
    )

    # Pick a free ephemeral port for the fake child (unit test isolation).
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        test_port = sock.getsockname()[1]

    revisions = {"value": generator_revision(root)}
    started_revs: list[str] = []
    children: list[subprocess.Popen] = []

    def command_factory(python_executable: str, _root: Path, port: int) -> list[str]:
        return [python_executable, str(child_script)]

    def popen(command, **kwargs):
        env = dict(kwargs.get("env") or {})
        env["APP_REVISION_EXPECT"] = revisions["value"]
        env["APP_PORT"] = str(test_port)
        env["CHILD_LOG"] = str(marker)
        kwargs["env"] = env
        started_revs.append(revisions["value"])
        process = subprocess.Popen(command, **kwargs)
        children.append(process)
        return process

    def stop_listeners(_port: int) -> list[int]:
        # Process may already be terminated by supervisor.stop_child(); ensure port is free.
        return []

    def wait_ready(_port: int, _timeout: float) -> bool:
        deadline = time.time() + 5
        while time.time() < deadline:
            if marker.is_file() and f"APP_REVISION={revisions['value']}" in marker.read_text(
                encoding="utf-8"
            ):
                return True
            time.sleep(0.05)
        return marker.is_file()

    config = SupervisorConfig(
        root=root,
        port=test_port,
        poll_seconds=0.05,
        debounce_seconds=0.05,
        ready_timeout_seconds=5.0,
        python_executable=__import__("sys").executable,
    )

    supervisor = LocalAppSupervisor(
        config,
        revision_fn=lambda _r: revisions["value"],
        command_factory=command_factory,
        popen=popen,
        stop_listeners=stop_listeners,
        wait_ready=wait_ready,
        sleep=time.sleep,
    )

    def mutate_and_stop():
        # Wait until first child is recorded.
        deadline = time.time() + 5
        while time.time() < deadline and len(started_revs) < 1:
            time.sleep(0.05)
        engine = root / "src" / "calendar_pedagoga" / "engine.py"
        engine.write_text("VALUE = 42\n", encoding="utf-8")
        revisions["value"] = generator_revision(root)
        deadline = time.time() + 10
        while time.time() < deadline and len(started_revs) < 2:
            time.sleep(0.05)
        supervisor.request_stop()

    worker = threading.Thread(target=mutate_and_stop, daemon=True)
    worker.start()
    supervisor.run_forever(max_iterations=200)
    worker.join(timeout=5)
    supervisor.stop_child()

    assert len(started_revs) >= 2
    assert started_revs[0] != started_revs[1]
    assert len(children) >= 2
    assert children[0].poll() is not None, "old child must be terminated"
    assert marker.read_text(encoding="utf-8").splitlines()[0].startswith("APP_REVISION=")
    assert supervisor.managed.revision == started_revs[-1]


def test_ui_generator_revision_uses_shared_module():
    from calendar_pedagoga import ui

    assert ui._generator_revision() == generator_revision(ROOT)
