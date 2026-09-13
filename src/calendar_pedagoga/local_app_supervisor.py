"""Local Streamlit supervisor: one process on :8501, restart on generator changes."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from calendar_pedagoga.generator_revision import generator_revision, project_root

LOGGER = logging.getLogger("local_app_supervisor")
DEFAULT_PORT = 8501
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_DEBOUNCE_SECONDS = 0.8
DEFAULT_READY_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class SupervisorConfig:
    root: Path
    port: int = DEFAULT_PORT
    poll_seconds: float = DEFAULT_POLL_SECONDS
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    ready_timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS
    python_executable: str | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
        force=True,
    )


def resolve_python(root: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        return str(venv_python)
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def build_streamlit_command(python_executable: str, root: Path, port: int) -> list[str]:
    return [
        python_executable,
        "-m",
        "streamlit",
        "run",
        str(root / "app.py"),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def listening_pids(port: int) -> list[int]:
    """Return PIDs that currently listen on TCP port (Windows-friendly)."""
    if sys.platform == "win32":
        return _listening_pids_windows(port)
    return _listening_pids_posix(port)


def _listening_pids_windows(port: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        LOGGER.warning("netstat failed: %s", exc)
        return []
    needle = f":{port}"
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        if "LISTENING" not in line.upper() or needle not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        # Match only host:port at the local address column.
        local_addr = parts[1] if len(parts) > 1 else ""
        if not local_addr.endswith(needle) and f"]{needle}" not in local_addr:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _listening_pids_posix(port: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return not listening_pids(port)


def wait_until_port_free(port: int, timeout_seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if port_is_free(port):
            return True
        time.sleep(0.2)
    return port_is_free(port)


def wait_until_http_ok(port: int, timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url_host = "127.0.0.1"
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((url_host, port), timeout=1.0):
                pass
            # Prefer urllib to avoid extra deps; Streamlit answers GET /.
            from urllib.error import URLError, HTTPError
            from urllib.request import urlopen

            try:
                with urlopen(f"http://{url_host}:{port}/", timeout=2.0) as response:
                    if 200 <= int(response.status) < 300:
                        return True
            except HTTPError as exc:
                if 200 <= int(exc.code) < 500:
                    return True
            except URLError:
                pass
        except OSError:
            pass
        time.sleep(0.4)
    return False


def terminate_pids(pids: Sequence[int], *, grace_seconds: float = 3.0) -> None:
    for pid in pids:
        _terminate_pid(pid)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        alive = [pid for pid in pids if _pid_alive(pid)]
        if not alive:
            return
        time.sleep(0.2)
    for pid in pids:
        if _pid_alive(pid):
            _kill_pid(pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def stop_port_listeners(port: int) -> list[int]:
    """Stop whatever currently listens on the app port. Return stopped PIDs."""
    pids = listening_pids(port)
    if not pids:
        return []
    LOGGER.info("Stopping listeners on port %s: %s", port, pids)
    terminate_pids(pids)
    if not wait_until_port_free(port, timeout_seconds=20.0):
        # Force remaining listeners once more.
        leftover = listening_pids(port)
        if leftover:
            LOGGER.warning("Force-killing leftover listeners: %s", leftover)
            for pid in leftover:
                _kill_pid(pid)
            wait_until_port_free(port, timeout_seconds=10.0)
    return pids


def child_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src = str(root / "src")
    existing = env.get("PYTHONPATH", "")
    parts = [part for part in existing.split(os.pathsep) if part and part != src]
    env["PYTHONPATH"] = os.pathsep.join([src, *parts]) if parts else src
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@dataclass
class ManagedApp:
    process: subprocess.Popen[str] | None
    revision: str
    command: list[str]


class LocalAppSupervisor:
    """Keep a single Streamlit child in sync with the generator revision."""

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        revision_fn: Callable[[Path], str] = generator_revision,
        command_factory: Callable[[str, Path, int], list[str]] = build_streamlit_command,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        stop_listeners: Callable[[int], list[int]] = stop_port_listeners,
        wait_ready: Callable[[int, float], bool] = wait_until_http_ok,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._revision_fn = revision_fn
        self._command_factory = command_factory
        self._popen = popen
        self._stop_listeners = stop_listeners
        self._wait_ready = wait_ready
        self._sleep = sleep
        self._managed = ManagedApp(process=None, revision="", command=[])
        self._stop_requested = False

    @property
    def managed(self) -> ManagedApp:
        return self._managed

    def request_stop(self) -> None:
        self._stop_requested = True

    def current_revision(self) -> str:
        return self._revision_fn(self.config.root)

    def start_child(self, revision: str | None = None) -> ManagedApp:
        rev = revision if revision is not None else self.current_revision()
        python_executable = resolve_python(self.config.root, self.config.python_executable)
        command = self._command_factory(python_executable, self.config.root, self.config.port)
        self._stop_listeners(self.config.port)
        LOGGER.info("APP_REVISION=%s", rev)
        LOGGER.info("Starting: %s", " ".join(command))
        process = self._popen(
            command,
            cwd=str(self.config.root),
            env=child_env(self.config.root),
            stdout=None,
            stderr=None,
            text=True,
        )
        self._managed = ManagedApp(process=process, revision=rev, command=command)
        ready = self._wait_ready(self.config.port, self.config.ready_timeout_seconds)
        if not ready:
            LOGGER.warning("Child started but HTTP readiness timed out on port %s", self.config.port)
        else:
            LOGGER.info("Child ready on http://127.0.0.1:%s", self.config.port)
        return self._managed

    def stop_child(self) -> list[int]:
        stopped: list[int] = []
        process = self._managed.process
        if process is not None and process.poll() is None:
            stopped.append(process.pid)
            terminate_pids([process.pid])
        stopped.extend(self._stop_listeners(self.config.port))
        # Unique preserve order
        unique: list[int] = []
        for pid in stopped:
            if pid not in unique:
                unique.append(pid)
        self._managed = ManagedApp(process=None, revision=self._managed.revision, command=self._managed.command)
        return unique

    def restart_child(self, revision: str | None = None) -> ManagedApp:
        old_pids = self.stop_child()
        LOGGER.info("OLD PROCESS TERMINATED: %s", old_pids or "none")
        return self.start_child(revision)

    def _stable_revision(self, initial: str) -> str | None:
        """Return revision only after it stays unchanged for debounce window."""
        latest = initial
        deadline = time.monotonic() + self.config.debounce_seconds
        while time.monotonic() < deadline:
            self._sleep(min(0.2, self.config.poll_seconds))
            current = self.current_revision()
            if current != latest:
                latest = current
                deadline = time.monotonic() + self.config.debounce_seconds
        return latest

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        iteration = 0
        self.start_child()
        while not self._stop_requested:
            if max_iterations is not None and iteration >= max_iterations:
                break
            iteration += 1
            self._sleep(self.config.poll_seconds)
            process = self._managed.process
            if process is not None and process.poll() is not None:
                LOGGER.warning("Child exited with code %s; restarting", process.returncode)
                self.start_child()
                continue
            current = self.current_revision()
            if current == self._managed.revision:
                continue
            stable = self._stable_revision(current)
            if stable is None or stable == self._managed.revision:
                continue
            LOGGER.info(
                "Generator files changed: %s -> %s; restarting Streamlit",
                self._managed.revision[:12],
                stable[:12],
            )
            self.restart_child(stable)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Streamlit supervisor for Календарь педагога")
    parser.add_argument("--root", type=Path, default=None, help="Project root (default: auto)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--debounce-seconds", type=float, default=DEFAULT_DEBOUNCE_SECONDS)
    parser.add_argument("--python", dest="python_executable", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else project_root()
    if args.port != DEFAULT_PORT:
        LOGGER.error("Only port %s is allowed for the local app", DEFAULT_PORT)
        return 2
    config = SupervisorConfig(
        root=root,
        port=args.port,
        poll_seconds=args.poll_seconds,
        debounce_seconds=args.debounce_seconds,
        python_executable=args.python_executable,
    )
    supervisor = LocalAppSupervisor(config)

    def _handle_signal(signum: int, _frame: object) -> None:
        LOGGER.info("Signal %s received; stopping", signum)
        supervisor.request_stop()
        supervisor.stop_child()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, _handle_signal)
        except (ValueError, OSError):
            pass

    try:
        supervisor.run_forever()
    finally:
        supervisor.stop_child()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
