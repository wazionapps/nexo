from __future__ import annotations

"""Shared prompt catalog for productized NEXO core automations."""

import os
import re
from functools import lru_cache
from pathlib import Path

_TOKEN_RE = re.compile(r"\[\[([a-zA-Z0-9_]+)\]\]")


def _find_templates_root(start: Path) -> Path | None:
    current = start.expanduser()
    try:
        current = current.resolve()
    except Exception:
        current = current.absolute()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "templates" / "core-prompts").is_dir():
            return candidate
    return None


def _resolve_repo_root() -> Path:
    configured = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
    resolved = _find_templates_root(configured)
    if resolved is not None:
        return resolved
    fallback = Path(__file__).resolve().parents[1]
    return _find_templates_root(fallback) or fallback


PROMPTS_DIR = _resolve_repo_root() / "templates" / "core-prompts"


@lru_cache(maxsize=None)
def load_core_prompt_template(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Core prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render_core_prompt(name: str, /, **values: object) -> str:
    template = load_core_prompt_template(name)
    required = {match.group(1) for match in _TOKEN_RE.finditer(template)}
    missing = sorted(key for key in required if key not in values)
    if missing:
        raise KeyError(f"Missing values for core prompt '{name}': {', '.join(missing)}")

    rendered = _TOKEN_RE.sub(lambda match: str(values[match.group(1)]), template)
    return rendered.rstrip("\n")


__all__ = [
    "PROMPTS_DIR",
    "load_core_prompt_template",
    "render_core_prompt",
]
