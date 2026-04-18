"""r13_pre_edit_guard — deterministic portion of the R13 rule.

Fase 2 spec item 0.14 (spike). R13 requires:

  Edit/Write/MultiEdit/NotebookEdit on path P
  AND no prior nexo_guard_check(files=P) in the last 30 tool calls
  AND no prior nexo_guard_check of any kind in the last 60s
  → inject prompt reminding the agent to guard_check first.

This module exposes the pure decision logic so both the headless engine
(enforcement_engine.py) and the Desktop JS twin can rely on the same
invariants without duplication. The classifier-driven portion of R13
(detecting "intent to edit" from free text, Mecanismo A cognitive_
sentiment) is out of scope for the spike; only actual tool_use events
are inspected here.

Why a separate module?
  - enforcement_engine.py stays focused on subprocess orchestration and
    stream parsing; rule logic lives beside its unit tests.
  - Desktop (nexo-desktop/lib/) can port this module to JS one-to-one.
  - Red-team tests (Fase 0.24) can exercise the decision logic in
    isolation without spinning up a subprocess.
"""
from __future__ import annotations

from dataclasses import dataclass


WATCHED_WRITE_TOOLS: frozenset[str] = frozenset({
    "Edit", "Write", "MultiEdit", "NotebookEdit", "Delete",
})

GUARD_CHECK_TOOLS: frozenset[str] = frozenset({
    "nexo_guard_check",
    "mcp__nexo__nexo_guard_check",
    "nexo_guard_file_check",
    "mcp__nexo__nexo_guard_file_check",
})

DEFAULT_WINDOW_SECONDS: float = 60.0
DEFAULT_WINDOW_CALLS: int = 30


@dataclass(frozen=True)
class ToolCallRecord:
    """A single tool call entry the enforcer tracked.

    ``tool`` is the normalised tool name (no ``mcp__nexo__`` prefix).
    ``ts`` is ``time.time()`` at call time. ``files`` is the list of
    absolute paths the call targeted, already normalised; may be empty
    for tools that do not touch files. ``meta`` is an optional mapping
    with tool-specific context — in particular, Bash calls record
    ``{"command": "<original bash command>"}`` so rules like R20
    (constant_change: did the operator grep first?) and R23d
    (chown -R: did the operator ls first?) can inspect prior calls
    that did not touch explicit files but whose command text carries
    the signal.
    """

    tool: str
    ts: float
    files: tuple[str, ...] = ()
    meta: dict | None = None


def _normalise(tool: str) -> str:
    return tool.replace("mcp__nexo__", "")


def should_inject_r13(
    current_tool: str,
    current_files: list[str] | tuple[str, ...],
    recent_calls: list[ToolCallRecord],
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    window_calls: int = DEFAULT_WINDOW_CALLS,
    current_ts: float | None = None,
) -> str | None:
    """Return an injection tag for R13 or None if R13 should stay quiet.

    Args:
        current_tool: Name of the tool the assistant is about to call.
        current_files: Paths the call will touch (can be empty).
        recent_calls: Ordered history of previous calls, oldest first.
        window_seconds: How far back a guard_check is considered fresh.
        window_calls: How many recent calls to scan for a matching guard.
        current_ts: Override "now" for deterministic tests. When None,
            uses the timestamp of the last recent call if available or
            ``time.time()`` otherwise.

    Returns:
        A string tag like ``"r13:<first_path>"`` if the rule fires, else
        None. The engine enqueues one injection per tag and dedupes.
    """
    tool = _normalise(current_tool)
    if tool not in WATCHED_WRITE_TOOLS:
        return None

    files = tuple(str(p) for p in current_files if p)
    if not files:
        # R13 requires a path target; edits without a path cannot be
        # blocked by a guard_check because guard_check operates on paths.
        # Still inject a generic reminder so the agent knows it must call
        # guard_check before structured writes.
        key = "r13:unknown-target"
    else:
        key = f"r13:{files[0]}"

    if current_ts is None:
        if recent_calls:
            current_ts = recent_calls[-1].ts
        else:
            import time as _time
            current_ts = _time.time()

    # Look backwards; earliest-first input means we iterate reversed.
    scanned = 0
    target_paths = {path for path in files}
    for record in reversed(recent_calls):
        if scanned >= window_calls:
            break
        scanned += 1
        if (current_ts - record.ts) > window_seconds:
            # Outside the time window → stop scanning; everything older
            # is also outside.
            break
        if _normalise(record.tool) not in GUARD_CHECK_TOOLS:
            continue
        if not target_paths:
            # current call has no specific files → any guard_check
            # counts; the agent already declared awareness.
            return None
        if any(path in target_paths for path in record.files):
            return None
        # guard_check happened but on different paths. Keep scanning.
    return key


__all__ = [
    "should_inject_r13",
    "WATCHED_WRITE_TOOLS",
    "GUARD_CHECK_TOOLS",
    "ToolCallRecord",
    "DEFAULT_WINDOW_SECONDS",
    "DEFAULT_WINDOW_CALLS",
]
