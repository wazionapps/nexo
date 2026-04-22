#!/usr/bin/env python3
"""Stable automation helper that routes prompts through the configured
NEXO backend (agent_runner / run_automation_text) instead of hardcoding
provider CLIs such as ``claude -p``.

Block E.6 / NF-DS-857651BA promoted this module from personal/scripts to
core so every NEXO install exposes the same primitive to its scripts,
plugins, and skills. The behaviour is unchanged from the personal copy;
only the import bootstrap learns both layouts:

  - repo checkout (``nexo/src/scripts/…``): ``_repo_root`` is
    ``nexo/`` and templates live at ``nexo/templates/``.
  - installed runtime (``~/.nexo/core/scripts/…``): ``_repo_root`` is
    ``~/.nexo/`` and templates live at ``~/.nexo/templates/``.

Both paths are probed so dev and live operators get identical behaviour.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # ``src`` in repo, ``core`` in runtime
_repo_root = _repo_src.parent   # ``nexo`` in repo, ``~/.nexo`` in runtime

if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"

# Templates live next to the code at repo time and at ``~/.nexo/templates``
# once installed. Probe both and surface whichever exists first so the
# helper works without the operator having to keep ``NEXO_HOME`` in sync
# with the repo checkout during development.
for _candidate in (_repo_root / "templates", NEXO_HOME / "templates"):
    _cand = str(_candidate)
    if _candidate.exists() and _cand not in sys.path:
        sys.path.insert(0, _cand)

try:
    from client_preferences import resolve_user_model
    _USER_MODEL = resolve_user_model()
except Exception:
    _USER_MODEL = ""

from nexo_helper import run_automation_text as _run_automation_text


def run_personal_automation_text(
    prompt: str,
    *,
    model: str = "",
    cwd: str = "",
    timeout: int = 21600,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    append_system_prompt: str = "",
) -> str:
    """Run ``prompt`` through the configured NEXO automation backend.

    ``model`` empty → use whichever model the operator's calibration has
    selected (``resolve_user_model``); providers that ignore the field
    (Claude Code bundled) stay happy with an empty string.
    ``cwd`` empty → inherit the current working directory.
    Every other kwarg passes through verbatim.
    """
    effective_model = model or _USER_MODEL or "opus"
    return _run_automation_text(
        prompt,
        model=effective_model,
        cwd=cwd or "",
        timeout=timeout,
        allowed_tools=allowed_tools,
        append_system_prompt=append_system_prompt,
    )


__all__ = [
    "DEFAULT_ALLOWED_TOOLS",
    "NEXO_HOME",
    "run_personal_automation_text",
]
