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

import inspect
import os
import re
import sys
import time
from pathlib import Path


_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # ``src`` in repo, ``core`` in runtime
_repo_root = _repo_src.parent   # ``nexo`` in repo, ``~/.nexo`` in runtime

if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"
DEFAULT_SHORT_TEXT_ALLOWED_TOOLS = ""
DEFAULT_SHORT_TEXT_TIMEOUT = max(
    30,
    int(os.environ.get("NEXO_PERSONAL_AUTOMATION_TIMEOUT", "180")),
)
_PROCESS_LOCK_COUNTS: dict[str, int] = {}

# Templates live next to the code at repo time and at ``~/.nexo/templates``
# once installed. Probe both and surface whichever exists first so the
# helper works without the operator having to keep ``NEXO_HOME`` in sync
# with the repo checkout during development.
for _candidate in (_repo_root / "templates", NEXO_HOME / "templates"):
    _cand = str(_candidate)
    if _candidate.exists() and _cand not in sys.path:
        sys.path.insert(0, _cand)

from nexo_helper import run_automation_text as _run_automation_text


def _infer_personal_caller() -> str:
    env_caller = str(os.environ.get("NEXO_AUTOMATION_CALLER") or "").strip()
    if env_caller:
        return env_caller
    candidates: list[Path] = []
    argv0 = str(sys.argv[0] or "").strip()
    if argv0:
        candidates.append(Path(argv0).expanduser())
    current = Path(__file__).resolve()
    for frame in inspect.stack()[1:]:
        try:
            path = Path(frame.filename).resolve()
        except Exception:
            continue
        if path != current:
            candidates.append(path)
    for candidate in candidates:
        parts = candidate.parts
        if "personal" in parts and "scripts" in parts:
            stem = candidate.stem.strip()
            if stem:
                return f"personal/{stem}"
    if argv0:
        stem = Path(argv0).stem.strip()
        if stem and stem not in {"python", "python3", "-m"}:
            return f"personal/{stem}"
    return "agent_run/generic"


def _caller_lock_path(caller: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", caller).strip("-") or "generic"
    return NEXO_HOME / "runtime" / "locks" / "personal-automation" / f"{slug}.lock"


def _read_lock_pid(path: Path) -> int:
    try:
        raw = path.read_text().splitlines()
    except Exception:
        return 0
    if not raw:
        return 0
    try:
        return int(raw[0].strip())
    except Exception:
        return 0


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_personal_caller_lock(caller: str) -> str:
    clean = str(caller or "").strip()
    if not clean.startswith("personal/"):
        return ""
    if _PROCESS_LOCK_COUNTS.get(clean, 0) > 0:
        _PROCESS_LOCK_COUNTS[clean] += 1
        return clean
    pid = os.getpid()
    lock_path = _caller_lock_path(clean)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing_pid = _read_lock_pid(lock_path)
            if existing_pid == pid:
                _PROCESS_LOCK_COUNTS[clean] = 1
                return clean
            if existing_pid and _pid_is_alive(existing_pid):
                raise RuntimeError(
                    f"Automation caller {clean} already has a live run (pid {existing_pid})."
                )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{pid}\n{int(time.time())}\n{clean}\n")
        _PROCESS_LOCK_COUNTS[clean] = 1
        return clean


def _release_personal_caller_lock(caller: str) -> None:
    clean = str(caller or "").strip()
    if not clean.startswith("personal/"):
        return
    count = _PROCESS_LOCK_COUNTS.get(clean, 0)
    if count > 1:
        _PROCESS_LOCK_COUNTS[clean] = count - 1
        return
    _PROCESS_LOCK_COUNTS.pop(clean, None)
    lock_path = _caller_lock_path(clean)
    if _read_lock_pid(lock_path) == os.getpid():
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def run_personal_automation_text(
    prompt: str,
    *,
    model: str = "",
    cwd: str = "",
    timeout: int = DEFAULT_SHORT_TEXT_TIMEOUT,
    allowed_tools: str = DEFAULT_SHORT_TEXT_ALLOWED_TOOLS,
    append_system_prompt: str = "",
    caller: str = "",
    tier: str = "",
    bare_mode: bool | None = True,
) -> str:
    """Run ``prompt`` through the configured NEXO automation backend.

    ``model`` stays empty unless the caller provides an explicit override.
    Backend/model/effort resolution belongs to the resonance engine via
    ``caller`` and ``tier``.
    ``cwd`` empty → inherit the current working directory.
    Every other kwarg passes through verbatim.
    """
    effective_caller = caller or _infer_personal_caller()
    lock_token = _acquire_personal_caller_lock(effective_caller)
    try:
        return _run_automation_text(
            prompt,
            model=model,
            cwd=cwd or "",
            timeout=timeout,
            allowed_tools=allowed_tools,
            append_system_prompt=append_system_prompt,
            include_bootstrap=False,
            caller=effective_caller,
            tier=tier,
            bare_mode=bare_mode,
        )
    finally:
        _release_personal_caller_lock(lock_token)


__all__ = [
    "DEFAULT_ALLOWED_TOOLS",
    "DEFAULT_SHORT_TEXT_ALLOWED_TOOLS",
    "DEFAULT_SHORT_TEXT_TIMEOUT",
    "NEXO_HOME",
    "run_personal_automation_text",
]
