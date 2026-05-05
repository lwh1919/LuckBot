"""Project-root-aware path helpers."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_project_root() -> Path:
    """Resolve the LuckBot project root independent of the current cwd."""
    raw = (os.getenv("LUCKBOT_PROJECT_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    anchor = Path(__file__).resolve()
    for candidate in (anchor.parent, *anchor.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "luckbot").is_dir():
            return candidate
    return Path.cwd().resolve()


def resolve_project_path(path: str | os.PathLike[str]) -> str:
    """Resolve a project-relative path to an absolute path."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return str(candidate.resolve())
    return str((resolve_project_root() / candidate).resolve())


__all__ = ["resolve_project_path", "resolve_project_root"]
