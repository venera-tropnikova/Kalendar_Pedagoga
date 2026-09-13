"""Working-tree identity of the calendar generator (not a Git revision)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def project_root() -> Path:
    """Repository root that contains app.py and src/."""
    return Path(__file__).resolve().parents[2]


def generator_paths(root: Path | None = None) -> list[Path]:
    """Files that define the loaded generator identity."""
    base = project_root() if root is None else Path(root)
    paths = [base / "app.py", *sorted((base / "src" / "calendar_pedagoga").glob("*.py"))]
    paths.append(base / "references" / "Календарный план Образец.docx")
    return paths


def generator_revision(root: Path | None = None) -> str:
    """SHA-256 over relative paths and file bytes of the generator set."""
    base = project_root() if root is None else Path(root)
    digest = hashlib.sha256()
    for path in generator_paths(base):
        digest.update(str(path.relative_to(base)).encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def generator_git_commit(root: Path | None = None) -> str:
    """Best-effort HEAD commit of the generator worktree."""

    base = project_root() if root is None else Path(root)
    try:
        completed = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    commit = (completed.stdout or "").strip()
    return commit or "unknown"


def generator_provenance(root: Path | None = None) -> dict[str, str]:
    """Hidden DOCX metadata payload for the loaded generator."""

    return {
        "GeneratorGitCommit": generator_git_commit(root),
        "GeneratorRevision": generator_revision(root),
        "GeneratedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
