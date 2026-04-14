from __future__ import annotations

"""Shared tree hygiene helpers for runtime/install/release flows."""

import re
from pathlib import Path


_DUPLICATE_COPY_RE = re.compile(r"^(?P<base>.+) (?P<copy>[2-9]\d*)$")
_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}


def canonical_artifact_name(name: str) -> str | None:
    """Return the canonical sibling name for a macOS-style duplicate copy."""
    path = Path(name)
    match = _DUPLICATE_COPY_RE.match(path.stem)
    if not match:
        return None
    return f"{match.group('base')}{path.suffix}"


def is_duplicate_artifact_name(path_like: str | Path) -> bool:
    """True when the path looks like a duplicate copy and its canonical sibling exists."""
    path = Path(path_like)
    canonical_name = canonical_artifact_name(path.name)
    if canonical_name is None:
        return False
    parent = path.parent
    if str(parent) in {"", "."} and not path.is_absolute():
        return False
    return path.with_name(canonical_name).exists()


def find_duplicate_artifact_paths(root: str | Path) -> list[Path]:
    """Find duplicate copy artifacts under a tree, skipping generated/vendor directories."""
    root_path = Path(root).resolve()
    duplicates: list[Path] = []
    for path in sorted(root_path.rglob("*")):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if is_duplicate_artifact_name(path):
            duplicates.append(path)
    return duplicates
