from __future__ import annotations

import signal
from pathlib import Path

import pytest

import render_api
from calendar_pedagoga.remote_generation import (
    EMBEDDED_GENERATION_ENV,
    GENERATION_API_TOKEN_ENV,
    GENERATION_API_URL_ENV,
)
from calendar_pedagoga.render_supervisor import (
    API_HOST,
    API_HOST_ENV,
    API_PORT,
    API_PORT_ENV,
    API_URL,
    build_child_env,
    resolve_token,
    serve,
    streamlit_command,
)

ROOT = Path(__file__).resolve().parents[1]


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stopped = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode


def _stop(process: _Proc) -> None:
    process.stopped = True
    process.returncode = -15


def test_api_bind_keeps_standalone_defaults(monkeypatch) -> None:
    monkeypatch.delenv(render_api.API_HOST_ENV, raising=False)
    monkeypatch.delenv(render_api.API_PORT_ENV, raising=False)
    monkeypatch.setenv("PORT", "10000")
    assert render_api.api_bind_host() == "0.0.0.0"
    assert render_api.api_bind_port() == 10000


def test_api_bind_explicit_host_and_port_override_public_port(monkeypatch) -> None:
    monkeypatch.setenv(render_api.API_HOST_ENV, "127.0.0.1")
    monkeypatch.setenv(render_api.API_PORT_ENV, "8000")
    monkeypatch.setenv("PORT", "10000")
    assert render_api.api_bind_host() == "127.0.0.1"
    assert render_api.api_bind_port() == 8000


def test_streamlit_command_binds_public_port() -> None:
    command = streamlit_command("python", ROOT, 10000)
    assert "--server.address" in command
    assert command[command.index("--server.address") + 1] == "0.0.0.0"
    assert command[command.index("--server.port") + 1] == "10000"


def test_minted_token_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    seen: dict[str, object] = {}

    def popen(command, cwd, env, **_kwargs):
        del cwd
        seen.setdefault("commands", []).append(command)
        seen["env"] = env
        return _Proc(pid=10 + len(seen["commands"]))

    caplog.set_level("INFO")
    code = serve(
        root=ROOT,
        port=10000,
        env={},
        python_executable="python",
        popen=popen,
        stop=_stop,
        wait_ready=lambda *_args: False,
        sleep=lambda _seconds: None,
    )
    assert code == 1
    token = seen["env"][GENERATION_API_TOKEN_ENV]
    assert token
    assert seen["env"][GENERATION_API_URL_ENV] == API_URL
    assert seen["env"][EMBEDDED_GENERATION_ENV] == "1"
    assert seen["env"][API_HOST_ENV] == API_HOST
    assert seen["env"][API_PORT_ENV] == str(API_PORT)
    rendered_commands = " ".join(" ".join(item) for item in seen["commands"])
    assert token not in rendered_commands
    assert token not in caplog.text
    assert len(seen["commands"]) == 1


def test_api_exit_stops_streamlit(caplog: pytest.LogCaptureFixture) -> None:
    processes: list[_Proc] = []

    def popen(*_args, **_kwargs):
        process = _Proc(pid=20 + len(processes))
        processes.append(process)
        return process

    def wait_ready(process, *_args):
        process.returncode = 1
        return True

    caplog.set_level("INFO")
    code = serve(
        root=ROOT,
        port=10000,
        env={GENERATION_API_TOKEN_ENV: "configured-token"},
        python_executable="python",
        popen=popen,
        stop=_stop,
        wait_ready=wait_ready,
        sleep=lambda _seconds: None,
    )
    assert code == 1
    assert len(processes) == 2
    assert processes[1].stopped is True
    assert "configured-token" not in caplog.text


def test_streamlit_exit_stops_api() -> None:
    processes: list[_Proc] = []

    def popen(*_args, **_kwargs):
        process = _Proc(pid=30 + len(processes))
        processes.append(process)
        return process

    def sleep(_seconds: float) -> None:
        processes[1].returncode = 1

    code = serve(
        root=ROOT,
        port=10000,
        env={GENERATION_API_TOKEN_ENV: "configured-token"},
        python_executable="python",
        popen=popen,
        stop=_stop,
        wait_ready=lambda *_args: True,
        sleep=sleep,
    )
    assert code == 1
    assert processes[0].stopped is True
    assert processes[1].returncode == 1


def test_sigterm_stops_both_processes() -> None:
    processes: list[_Proc] = []

    def popen(*_args, **_kwargs):
        process = _Proc(pid=40 + len(processes))
        processes.append(process)
        return process

    def sleep(_seconds: float) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    code = serve(
        root=ROOT,
        port=10000,
        env={GENERATION_API_TOKEN_ENV: "configured-token"},
        python_executable="python",
        popen=popen,
        stop=_stop,
        wait_ready=lambda *_args: True,
        sleep=sleep,
    )
    assert code == 0
    assert processes[0].stopped is True
    assert processes[1].stopped is True
    assert signal.getsignal(signal.SIGINT) is not None


def test_child_env_does_not_drop_existing_token() -> None:
    token, created = resolve_token({GENERATION_API_TOKEN_ENV: "already-set"})
    assert created is False
    assert token == "already-set"
    env = build_child_env({GENERATION_API_TOKEN_ENV: token, "PORT": "10000"}, token, ROOT)
    assert env[GENERATION_API_TOKEN_ENV] == token
    assert env["PORT"] == "10000"
    assert env[EMBEDDED_GENERATION_ENV] == "1"
