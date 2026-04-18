"""R23i — auto-deploy trigger ignored after a git push.

Pure decision module. Part of Fase D2 (soft).

When a git push lands on a repo whose project entity has
`deploy.auto_deploy=true` and the operator then runs Edit/Write on the
same repo tree within a short window, R23i warns — that Edit will be
clobbered by the next auto-deploy. The usual story: forgetting to
re-push after a local fix.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23i auto-deploy after recent push: project '{project}' has "
    "auto_deploy=true and you just pushed. The edit you are about to "
    "make to '{path}' will be overwritten on the next deploy unless "
    "you also commit+push it. Either commit the change or disable "
    "auto-deploy temporarily."
)


_GIT_PUSH_RE = re.compile(r"\bgit\s+push\b", re.IGNORECASE)


def extract_push(cmd: str) -> bool:
    if not cmd or not isinstance(cmd, str):
        return False
    return bool(_GIT_PUSH_RE.search(cmd))


def should_inject_r23i(
    tool_name: str,
    tool_input,
    *,
    current_project: dict | None,
    recent_push: bool,
) -> tuple[bool, str]:
    """Fire on an Edit/Write against a project that just saw a push."""
    if tool_name not in {"Edit", "Write", "MultiEdit"}:
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not path:
        return False, ""
    if not current_project:
        return False, ""
    deploy = (current_project.get("deploy") or {}) if isinstance(current_project, dict) else {}
    if not deploy.get("auto_deploy"):
        return False, ""
    if not recent_push:
        return False, ""
    # Require the edit to live under the project's local_path so we
    # don't nag about unrelated tools.
    local = (current_project.get("local_path") or "").rstrip("/")
    if local and not (path == local or path.startswith(local + "/")):
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(
        project=current_project.get("name", "?"),
        path=path,
    )
    return True, prompt
