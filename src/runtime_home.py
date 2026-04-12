from __future__ import annotations

"""Shared helpers to resolve the managed NEXO home path."""

import os
from pathlib import Path


def user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def managed_nexo_home(*, home: Path | None = None) -> Path:
    return (home or user_home()) / ".nexo"


def legacy_nexo_home(*, home: Path | None = None) -> Path:
    return (home or user_home()) / "claude"


def resolve_nexo_home(value: str | os.PathLike[str] | None = None) -> Path:
    home = user_home()
    managed = managed_nexo_home(home=home)
    candidate = Path(value).expanduser() if value else Path(
        os.environ.get("NEXO_HOME", str(managed))
    ).expanduser()
    legacy = legacy_nexo_home(home=home)

    if candidate == managed:
        return managed
    if candidate == legacy:
        return managed if managed.exists() or legacy.is_symlink() else candidate

    try:
        if managed.exists() and candidate.resolve(strict=False) == managed.resolve(strict=False):
            return managed
    except Exception:
        pass

    return candidate


def export_resolved_nexo_home(value: str | os.PathLike[str] | None = None) -> Path:
    resolved = resolve_nexo_home(value)
    os.environ["NEXO_HOME"] = str(resolved)
    return resolved
