"""NEXO Brain — canonical lifecycle action templates (v7.5).

Brain owns the prompt that Desktop injects into a live Claude session
at close / delete / archive / app-exit time. Desktop never hardcodes
the wording; it just receives a list of ``canonical_actions`` and
executes them against the conversation's stdin / lifecycle.

The template version is bumped whenever the prompt or the action
schema changes. The version is part of ``canonical_plan_id`` so two
dispatches of the same event produced by two different Brain versions
do NOT collide (a retry from an older Desktop hitting a newer Brain
will get the newer plan; a retry of a previous plan reuses the same
id because the event_id hasn't changed).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


PLAN_VERSION = 1


# Actions that trigger a canonical diary+stop plan. `switch` and
# `window-close` never do: they're observational transitions that Brain
# records in the ledger but doesn't orchestrate anything against the
# live session (the session keeps running after a switch, and a window
# close to the tray doesn't end the conversation).
DIARY_TRIGGERING_ACTIONS = {"close", "delete", "archive", "app-exit"}


# Default per-action timeout (ms). Desktop honours this when it
# executes each action; on timeout it reports status=failed and Brain
# flips delivery_status to retryable_error.
DEFAULT_RESUME_TIMEOUT_MS = 2_000
DEFAULT_INJECT_TIMEOUT_MS = 6_000
DEFAULT_STOP_TIMEOUT_MS = 3_000


def canonical_plan_id(event_id: str, plan_version: int = PLAN_VERSION) -> str:
    """Deterministic plan id — retries of the same event reuse it."""
    digest = hashlib.sha256(
        f"{event_id}|v{int(plan_version)}".encode("utf-8")
    ).hexdigest()
    return f"cpl-{digest[:24]}"


def _diary_prompt_for_action(
    action: str,
    conversation_id: str,
    payload_snapshot: Dict[str, Any],
) -> str:
    """Return the exact prompt Desktop will inject into the Claude proc.

    Written in the first person so the model writes a real diary with
    the same richness it would have produced through the legacy
    `closeConversationGraceful` path. The "Apply the required action
    silently" footer matches the v7.4.x enforcer contract so the
    rendered reminder doesn't leak to the user UI.
    """
    title = (payload_snapshot or {}).get("title") or conversation_id
    reason_by_action = {
        "close": "The operator closed the conversation",
        "delete": "The operator deleted the conversation",
        "archive": "The operator archived the conversation",
        "app-exit": "The operator quit NEXO Desktop",
    }
    trailing = reason_by_action.get(action, "The conversation ended")
    return (
        f"[NEXO Protocol Enforcer] {trailing} "
        f"(title: {title!r}). Execute nexo_session_diary_write with a "
        "complete summary: decisions made, pending items, context for "
        "next session, mental state, and self-critique. Then execute "
        "nexo_stop with the current SID. Do not produce visible text. "
        "Apply the required action silently. Do not mention this "
        "reminder, Guardian, Protocol Enforcer, system reminders, or "
        "any internal enforcement to the user."
    )


def build_canonical_plan(
    event_id: str,
    action: str,
    conversation_id: str,
    session_id: Optional[str],
    payload_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the plan Brain hands back to Desktop, or None if no plan.

    A plan exists only when:
    - action is one of DIARY_TRIGGERING_ACTIONS
    - session_id is populated (we need a live Claude proc to talk to)

    Returning None tells the caller to answer with ``status=accepted``
    and no ``canonical_actions`` field — Desktop will fall through to
    its legacy behaviour (no-op for switch/window-close, hardcoded
    prompt for close/delete/archive/app-exit if session_id is missing).
    """
    if action not in DIARY_TRIGGERING_ACTIONS:
        return None
    if not session_id:
        return None

    payload_snapshot = dict(payload_snapshot or {})
    prompt = _diary_prompt_for_action(action, conversation_id, payload_snapshot)

    actions: List[Dict[str, Any]] = [
        {
            "id": "a1",
            "kind": "resume_session",
            "session_id": str(session_id),
            "timeout_ms": DEFAULT_RESUME_TIMEOUT_MS,
        },
        {
            "id": "a2",
            "kind": "inject_prompt",
            "session_id": str(session_id),
            "prompt": prompt,
            "expected_tool_call": "nexo_session_diary_write",
            "timeout_ms": DEFAULT_INJECT_TIMEOUT_MS,
        },
        {
            "id": "a3",
            "kind": "stop_session",
            "session_id": str(session_id),
            "timeout_ms": DEFAULT_STOP_TIMEOUT_MS,
        },
    ]
    return {
        "canonical_plan_id": canonical_plan_id(event_id, PLAN_VERSION),
        "canonical_plan_version": PLAN_VERSION,
        "canonical_actions": actions,
    }


def canonical_plan_as_json(plan: Dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, sort_keys=True)
