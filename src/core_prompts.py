from __future__ import annotations

"""Shared prompt catalog for productized NEXO core automations."""

import os
import re
from functools import lru_cache
from pathlib import Path

_TOKEN_RE = re.compile(r"\[\[([a-zA-Z0-9_]+)\]\]")


def _resolve_repo_root() -> Path:
    candidate = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent))).expanduser().resolve()
    if candidate.name == "src":
        return candidate.parent
    if (candidate / "templates").is_dir():
        return candidate
    if (candidate.parent / "templates").is_dir():
        return candidate.parent
    return Path(__file__).resolve().parents[1]


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

    return _TOKEN_RE.sub(lambda match: str(values[match.group(1)]), template)


__all__ = [
    "PROMPTS_DIR",
    "load_core_prompt_template",
    "render_core_prompt",
]
