"""R23c — destructive Bash executed in wrong cwd.

Pure decision module. Part of Fase D2 (soft).

When Bash runs a destructive verb (git reset --hard, git clean,
git push --force — already covered by R23e/git_destructive —, rm -rf,
terraform destroy, etc.) the engine cross-checks the current working
directory against the project.local_path registered for the context
the user is currently discussing. A mismatch produces a warning.

Parity with Fase C R23 style: returns (should_inject, prompt). Uses
only command + cwd + list of (project_name, local_path) entries. The
engine resolves cwd from `process.cwd()` / `os.getcwd()` or the
explicit `cwd=` arg of the bash tool_input.
"""
from __future__ import annotations

import os
import re


INJECTION_PROMPT_TEMPLATE = (
    "R23c destructive command in unexpected cwd: '{cmd}' runs inside "
    "'{cwd}'. You are currently discussing project '{project}' whose "
    "local_path is '{expected}'. Confirm the cwd is correct before "
    "proceeding — a destructive verb executed in the wrong tree is the "
    "most common source of accidental damage."
)


DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|\-\-force\s+|-r[a-zA-Z]*\s+)[^/\s-]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf?\s+/", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-fd?x?\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bterraform\s+destroy\b", re.IGNORECASE),
    re.compile(r"\bmake\s+(distclean|mrproper)\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+system\s+prune\b", re.IGNORECASE),
]


def _matches_destructive(cmd: str) -> bool:
    return any(p.search(cmd) for p in DESTRUCTIVE_PATTERNS)


def _normalize_path(p: str) -> str:
    return os.path.normpath(os.path.expanduser(p or ""))


def should_inject_r23c(
    tool_name: str,
    tool_input,
    cwd: str,
    current_project: dict | None,
) -> tuple[bool, str]:
    """Fire when destructive cmd's cwd mismatches current project local_path."""
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return False, ""
    if not _matches_destructive(cmd):
        return False, ""
    if not current_project:
        return False, ""
    expected = _normalize_path((current_project or {}).get("local_path") or "")
    if not expected:
        return False, ""
    actual = _normalize_path(cwd or os.getcwd())
    if not actual:
        return False, ""
    # Allow cwd that lives inside expected tree.
    if actual == expected or actual.startswith(expected + os.sep):
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(
        cmd=cmd.strip()[:160],
        cwd=actual,
        project=current_project.get("name", "?"),
        expected=expected,
    )
    return True, prompt
