"""One Render container: private Generation API, then public Streamlit."""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from calendar_pedagoga.generator_revision import project_root
from calendar_pedagoga.remote_generation import (
    EMBEDDED_GENERATION_ENV,
    GENERATION_API_TOKEN_ENV,
    GENERATION_API_URL_ENV,
)

LOGGER = logging.getLogger("render_supervisor")

API_HOST = "127.0.0.1"
API_PORT = 8000
API_URL = f"http://{API_HOST}:{API_PORT}"
API_HOST_ENV = "CALENDAR_GENERATION_API_HOST"
API_PORT_ENV = "CALENDAR_GENERATION_API_PORT"
DEFAULT_READY_TIMEOUT_SECONDS = 120.0
_POLL_SECONDS = 0.25

PopenFactory = Callable[..., subprocess.Popen]
StopProcess = Callable[[subprocess.Popen], None]
WaitReady = Callable[[subprocess.Popen, str, float], bool]
Sleep = Callable[[float], None]


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )


def resolve_token(env: Mapping[str, str]) -> tuple[str, bool]:
    """Return the process token and whether it was created here."""

    configured = (env.get(GENERATION_API_TOKEN_ENV) or "").strip()
    if configured:
        return configured, False
    return secrets.token_urlsafe(32), True


def build_child_env(base: Mapping[str, str], token: str, root: Path) -> dict[str, str]:
    env = dict(base)
    src = str(root / "src")
    existing = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part and part != src]
    env["PYTHONPATH"] = os.pathsep.join([src, *existing]) if existing else src
    env[GENERATION_API_TOKEN_ENV] = token
    env[GENERATION_API_URL_ENV] = API_URL
    env[EMBEDDED_GENERATION_ENV] = "1"
    env[API_HOST_ENV] = API_HOST
    env[API_PORT_ENV] = str(API_PORT)
    return env


def api_command(python_executable: str, root: Path) -> list[str]:
    return [python_executable, str(root / "render_api.py")]


def streamlit_command(python_executable: str, root: Path, port: int) -> list[str]:
    return [
        python_executable,
        "-m",
        "streamlit",
        "run",
        str(root / "app.py"),
        "--server.address",
        "0.0.0.0",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def wait_for_api(
    process: subprocess.Popen,
    url: str,
    timeout: float,
    *,
    sleep: Sleep = time.sleep,
) -> bool:
    deadline = time.monotonic() + timeout
    health = url.rstrip("/") + "/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            LOGGER.error("Generation API exited before it became ready")
            return False
        try:
            with urlopen(health, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("status") == "ok":
                LOGGER.info("Generation API is ready")
                return True
        except (OSError, URLError, json.JSONDecodeError, UnicodeError, TimeoutError):
            pass
        sleep(0.2)
    LOGGER.error("Generation API readiness timed out")
    return False


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _popen_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def serve(
    *,
    root: Path | None = None,
    port: int | None = None,
    env: Mapping[str, str] | None = None,
    python_executable: str | None = None,
    ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
    popen: PopenFactory | None = None,
    stop: StopProcess = stop_process,
    wait_ready: WaitReady | None = None,
    sleep: Sleep = time.sleep,
) -> int:
    """Start the API, then Streamlit. Return 0 on signal, 1 if a child dies."""

    project = project_root() if root is None else Path(root)
    public_port = port if port is not None else int(os.environ.get("PORT", "8501"))
    parent_env = dict(os.environ if env is None else env)
    token, created = resolve_token(parent_env)
    if created:
        LOGGER.info("Internal generation token created")
    else:
        LOGGER.info("Using configured generation token")
    child_env = build_child_env(parent_env, token, project)
    executable = python_executable or sys.executable
    launch = popen or subprocess.Popen
    ready = wait_ready or (
        lambda process, url, timeout: wait_for_api(process, url, timeout, sleep=sleep)
    )
    state = {"stop": False}

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s", signum)
        state["stop"] = True

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    api: subprocess.Popen | None = None
    web: subprocess.Popen | None = None
    try:
        LOGGER.info("Starting Generation API on %s:%s", API_HOST, API_PORT)
        api = launch(
            api_command(executable, project),
            cwd=str(project),
            env=child_env,
            **_popen_kwargs(),
        )
        if not ready(api, API_URL, ready_timeout):
            stop(api)
            return 1
        LOGGER.info("Starting Streamlit on 0.0.0.0:%s", public_port)
        web = launch(
            streamlit_command(executable, project, public_port),
            cwd=str(project),
            env=child_env,
            **_popen_kwargs(),
        )
        while True:
            if state["stop"]:
                LOGGER.info("Stopping both processes")
                stop(web)
                stop(api)
                return 0
            if api.poll() is not None:
                LOGGER.error("Generation API exited")
                stop(web)
                return 1
            if web.poll() is not None:
                LOGGER.error("Streamlit exited")
                stop(api)
                return 1
            sleep(_POLL_SECONDS)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    configure_logging()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
