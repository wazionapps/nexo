"""NEXO DB — Task classification storage (internal + owner).

Migration #40 added ``internal`` and ``owner`` columns to ``followups`` and
``reminders``. Agents creating or updating tasks pass these two fields
explicitly via the MCP tools (``nexo_followup_create``, ``nexo_reminder_create``
and their ``_update`` counterparts).

The Brain core does **not** classify tasks on behalf of agents. Up to and
including v5.8.1 the core shipped a Spanish-first regex heuristic
(``NF-PROTOCOL-*`` / ``NF-DS-*`` prefixes, user verbs like ``debes``,
``revisar``, etc.) as a fallback for callers that left the fields blank.
That fallback bled NEXO-specific naming conventions into every deployment
of the shared Brain — third-party agents plugged into the same DB would
inherit classifications they never asked for. v5.8.2 removes it.

The module now exposes only:

    VALID_OWNERS      — the canonical set {user, waiting, agent, shared}.
    normalise_owner   — clamps an agent-supplied string to VALID_OWNERS
                        (or ``None`` for empty / invalid input so the
                        caller can decide whether to persist ``NULL``).
    normalise_internal — coerces truthy / boolean / numeric agent input
                        into ``0`` / ``1`` (or ``None`` for empty input).

    owner values:
        'user'    — the user has to act.
        'waiting' — blocked on an external response.
        'agent'   — the AI agent handles it autonomously. Intentionally
                    named ``agent`` (not ``nexo``) so deployments render
                    whatever assistant label fits client-side.
        'shared'  — collaborative follow-up.
        NULL      — unclassified; clients are free to apply whatever
                    fallback they want at render time.

Clients that want automatic classification (NEXO Desktop does, via its
``_legacyClassifyOwner`` / ``_legacyIsInternalTaskId`` helpers) compute
``owner``/``internal`` themselves and pass them to the create/update call.
"""

from __future__ import annotations


VALID_OWNERS = {"user", "waiting", "agent", "shared"}


def normalise_owner(value: str | None) -> str | None:
    """Accept owner overrides from agents and clamp to VALID_OWNERS.

    Returns None for empty input (so the DB keeps NULL / pre-existing value)
    and coerces invalid strings to None rather than silently persisting
    garbage.
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
