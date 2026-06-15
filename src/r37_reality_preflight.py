"""r37_reality_preflight — require live context before sensitive answers."""
from __future__ import annotations

import re

from core_prompts import render_core_prompt

INJECTION_PROMPT_TEMPLATE = render_core_prompt("r37-reality-preflight-injection")

SENSITIVE_QUERY_RE = re.compile(
    r"\b("
    r"producto|product|proyecto|project|credencial(?:es)?|credential(?:s)?|"
    r"clave(?:s)?|secret(?:o|s)?|token(?:s)?|servidor(?:es)?|server(?:s)?|"
    r"puerto(?:s)?|port(?:s)?|dns|ip|release|versi[oó]n|version|commit|"
    r"branch|rama|tag|deploy|despliegue|memoria|memory|local|archivo(?:s)?|"
    r"file(?:s)?|repo|c[oó]digo|code|docs?|documentaci[oó]n"
    r")\b",
    re.IGNORECASE,
)

REALITY_TOOL_NAMES = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "Bash",
        "nexo_system_catalog",
        "nexo_pre_action_context",
        "nexo_recent_context",
        "nexo_session_diary_read",
        "nexo_change_log",
        "nexo_status",
        "nexo_followup_get",
        "nexo_reminders",
        "nexo_credential_get",
        "nexo_credential_list",
        "nexo_tool_explain",
    }
)


def query_needs_reality_preflight(user_text: str) -> bool:
    return bool(SENSITIVE_QUERY_RE.search(user_text or ""))


def _record_text(record) -> str:
    parts: list[str] = []
    for value in getattr(record, "files", ()) or ():
        parts.append(str(value))
    meta = getattr(record, "meta", None)
    if isinstance(meta, dict):
        for value in meta.values():
            parts.append(str(value))
    return "\n".join(parts).lower()


def record_is_reality_check(record) -> bool:
    tool = str(getattr(record, "tool", "") or "").replace("mcp__nexo__", "")
    if tool in REALITY_TOOL_NAMES:
        return True
    text = _record_text(record)
    return any(
        marker in text
        for marker in (
            "project-atlas.json",
            "/docs/",
            "docs/",
            "git status",
            "git show",
            "git log",
            "curl ",
            "dig ",
            "nexo.db",
        )
    )


def recent_reality_check_loaded(recent_tool_records, *, window_calls: int = 8) -> bool:
    if not recent_tool_records:
        return False
    return any(record_is_reality_check(record) for record in list(recent_tool_records)[-window_calls:])


def should_inject_r37(user_text: str, recent_tool_records) -> bool:
    return query_needs_reality_preflight(user_text) and not recent_reality_check_loaded(recent_tool_records)


__all__ = [
    "INJECTION_PROMPT_TEMPLATE",
    "query_needs_reality_preflight",
    "record_is_reality_check",
    "recent_reality_check_loaded",
    "should_inject_r37",
]
