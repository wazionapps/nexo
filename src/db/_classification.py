"""NEXO DB — Task classification helpers (internal + owner).

Introduced in migration #40. Every followup and reminder carries two
classification attributes so clients (Desktop Home, dashboard, future
agents) do not need to compute them with client-side regex:

    internal (INTEGER 0/1):
        1 if the task is bookkeeping the agent keeps for itself
        (protocol enforcer, deep-sleep housekeeping, audit trail,
        release gates, retroactive learnings). These are hidden from
        normal user views by default.

    owner (TEXT):
        'user'    — the user has to act (was 'Para ti' in Desktop).
        'waiting' — blocked on an external response (was 'Esperando').
        'agent'   — the AI agent handles it autonomously. Intentionally
                    named 'agent' and NOT 'nexo' so non-NEXO deployments
                    render whatever label fits (e.g. 'Claude', 'Codex',
                    hotel-assistant name). The user-facing label is
                    resolved client-side.
        'shared'  — collaborative follow-up (was 'Seguimiento').
        NULL      — unclassified; clients fall back to the legacy
                    client-side heuristic for backward compat.

Agents creating tasks via nexo_followup_create / nexo_reminder_create
can override both fields explicitly. If they leave them blank, the
Brain applies the heuristic below so a vanilla agent keeps sensible
behaviour out of the box.
"""

from __future__ import annotations

import re

# Task-ID prefixes historically owned by NEXO's own automation. They are
# kept as a default heuristic because they match the existing corpus of
# 468+ followups and 40+ reminders. Any agent not following this naming
# convention will simply not match these patterns and its tasks will
# stay visible (internal=0) unless the agent sets internal=1 explicitly
# on create — which is exactly what we want for a pluralistic ecosystem.
_INTERNAL_ID_PATTERNS = [
    re.compile(r"^NF-PROTOCOL[-_]", re.IGNORECASE),
    re.compile(r"^NF-DS[-_]", re.IGNORECASE),
    re.compile(r"^NF-AUDIT[-_]", re.IGNORECASE),
    re.compile(r"^NF-OPPORTUNITY[-_]", re.IGNORECASE),
    re.compile(r"^NF-RETRO[-_]", re.IGNORECASE),
    re.compile(r"^R-RELEASE[-_]", re.IGNORECASE),
    re.compile(r"^R-FU-NF-PROTOCOL[-_]", re.IGNORECASE),
    re.compile(r"^R-FU-NF-DS[-_]", re.IGNORECASE),
    re.compile(r"^R-FU-NF-AUDIT[-_]", re.IGNORECASE),
]

# Spanish user-action verbs. The heuristic is Spanish-first because the
# existing corpus is Spanish, but since every agent can override `owner`
# explicitly on create, deployments in other languages are not blocked.
_USER_VERB_RX = re.compile(
    r"\b(francisco debe|debes|llamar|responder|revisar|validar|confirmar|"
    r"decidir|aprobar|firmar|enviar email|mandar email|contestar|"
    r"reuni[óo]n|reservar|comprar)\b",
    re.IGNORECASE,
)

_WAITING_RX = re.compile(
    r"\b(esperando|esperar|bloqueo|bloqueado|pendiente respuesta|"
    r"pendiente de|en espera)\b",
    re.IGNORECASE,
)

_AGENT_RX = re.compile(
    r"\b(monitoreo|monitorizar|monitor|auditor[íi]a diaria|"
    r"promoci[óo]n diaria|seguir|seguimiento 24|72h|checkpoint|runner|cron)\b",
    re.IGNORECASE,
)

VALID_OWNERS = {"user", "waiting", "agent", "shared"}


def is_internal_id(task_id: str | None) -> bool:
    """Return True when the ID matches a known agent-internal prefix."""
    tid = (task_id or "").strip()
    if not tid:
        return False
    return any(pat.search(tid) for pat in _INTERNAL_ID_PATTERNS)


def classify_owner(
    task_id: str | None,
    description: str | None,
    category: str | None = None,
    recurrence: str | None = None,
) -> str:
    """Classify ownership into one of VALID_OWNERS using the legacy rules."""
    tid = (task_id or "").strip()
    desc = (description or "").strip()
    cat = (category or "").strip().lower()
    rec = (recurrence or "").strip()

    if cat == "waiting" or _WAITING_RX.search(desc):
        return "waiting"
    if _USER_VERB_RX.search(desc) or tid.lower().startswith("nf-protocol-"):
        return "user"
    if rec or _AGENT_RX.search(desc):
        return "agent"
    return "shared"


def classify_task(
    task_id: str | None,
    description: str | None,
    category: str | None = None,
    recurrence: str | None = None,
) -> tuple[int, str]:
    """Compute (internal, owner) pair for a task.

    Returns integers for internal so the SQLite column (INTEGER DEFAULT 0)
    and the JSON round-trip stay consistent. Clients can truthy-check either
    int or bool safely.
    """
    internal = 1 if is_internal_id(task_id) else 0
    owner = classify_owner(task_id, description, category, recurrence)
    return internal, owner


def normalise_owner(value: str | None) -> str | None:
    """Accept owner overrides from agents and clamp to VALID_OWNERS.

    Returns None for empty input (so the DB keeps NULL / pre-existing value)
    and coerces invalid strings to None rather than silently persisting
    garbage. Callers decide whether to fall back to classify_owner().
    """
    if value is None:
        return None
    normalised = str(value).strip().lower()
    if not normalised:
        return None
    return normalised if normalised in VALID_OWNERS else None


def normalise_internal(value) -> int | None:
    """Coerce agent-supplied internal flag into {0, 1} or None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) != 0 else 0
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "on", "internal"}:
        return 1
    if text in {"0", "false", "no", "n", "off", "external", "public"}:
        return 0
    return None
