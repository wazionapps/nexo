"""MCP tools for automation session logging.

Gives NEXO Desktop (which launches ``claude`` directly, not via
``agent_runner.run_automation_prompt``) a way to record its interactive
sessions in the same ``automation_runs`` table as every other backend call.

Two tools:

    nexo_session_log_create  → INSERT a row with ended_at=NULL, return id
    nexo_session_log_close   → UPDATE the row with exit + duration + tokens

The tools are intentionally thin wrappers over the helpers in
``agent_runner``. They exist as MCP tools so clients that don't embed the
Python runtime (Desktop's TypeScript/Electron process, any future
third-party agent) can still participate in the unified log.
"""
from __future__ import annotations

from pathlib import Path


def handle_session_log_create(payload: dict | None = None, **kwargs) -> dict:
    """Open an automation session row.

    Expected arguments (all optional except ``caller`` and ``backend``):
        caller          — e.g. "desktop_new_session" (registered in
                          resonance_map.py).
        backend         — "claude_code" or "codex".
        session_type    — "interactive_chat" | "interactive_desktop"
                          (default: "interactive_desktop").
        model           — concrete model string, if the client already
                          resolved it.
        reasoning_effort — concrete effort string.
        resonance_tier  — tier label for traceability.
        cwd             — working directory the session is anchored to.
        pid             — the child PID if the client already has it.
        context_excerpt — optional short preview of the first prompt
                          (truncated to 2048 chars for prompt_chars).
    """
    args = payload or kwargs or {}
    caller = str(args.get("caller") or "").strip()
    backend = str(args.get("backend") or "").strip()
    if not caller or not backend:
        return {
            "ok": False,
            "error": "caller and backend are required",
            "session_id": None,
        }

    session_type = str(args.get("session_type") or "interactive_desktop").strip()
    model = str(args.get("model") or "").strip()
    reasoning_effort = str(args.get("reasoning_effort") or "").strip()
    resonance_tier = str(args.get("resonance_tier") or "").strip()
    cwd = Path(str(args.get("cwd") or ".")).expanduser()
    pid_raw = args.get("pid")
    try:
        pid = int(pid_raw) if pid_raw is not None and pid_raw != "" else None
    except (TypeError, ValueError):
        pid = None

    context_excerpt = str(args.get("context_excerpt") or "")[:2048]

    # Resolve resonance_tier if client did not pre-compute it.
    if not resonance_tier and caller:
        try:
            from resonance_map import (
                resolve_tier_for_caller,
                UnregisteredCallerError,
            )
            try:
                from client_preferences import load_client_preferences
                prefs = load_client_preferences()
            except Exception:
                prefs = {}
            user_default = ""
            if isinstance(prefs, dict):
                user_default = str(prefs.get("default_resonance") or "").strip()
            try:
                resonance_tier = resolve_tier_for_caller(
                    caller, user_default=user_default or None
                )
            except UnregisteredCallerError:
                resonance_tier = ""
        except Exception:
            resonance_tier = ""

    from agent_runner import _record_automation_start

    row_id, err = _record_automation_start(
        caller=caller,
        backend=backend,
        session_type=session_type,
        task_profile="",
        model=model,
        reasoning_effort=reasoning_effort,
        resonance_tier=resonance_tier,
        cwd=cwd,
        output_format="interactive",
        prompt=context_excerpt,
        pid=pid,
    )
    if row_id is None:
        return {"ok": False, "error": err or "session log insert failed", "session_id": None}
    return {
        "ok": True,
        "session_id": int(row_id),
        "resonance_tier": resonance_tier,
    }


def handle_session_log_close(payload: dict | None = None, **kwargs) -> dict:
    """Close an automation session row opened by nexo_session_log_create.

    Expected arguments:
        session_id    — int returned by the create call.
        returncode    — exit code (default 0).
        duration_ms   — total wall-clock duration in ms.
        input_tokens, cached_input_tokens, output_tokens — counters from
                         the client's own telemetry, all optional.
        total_cost_usd — float, optional.
        telemetry_source — short label ("desktop_stream", "codex_json", ...).
        error         — short error string if the session failed.
    """
    args = payload or kwargs or {}
    sid_raw = args.get("session_id")
    try:
        session_id = int(sid_raw) if sid_raw is not None else None
    except (TypeError, ValueError):
        session_id = None
    if session_id is None:
        return {"ok": False, "error": "session_id is required"}

    returncode = int(args.get("returncode") or 0)
    duration_ms = int(args.get("duration_ms") or 0)
    telemetry = {
        "usage": {
            "input_tokens": int(args.get("input_tokens") or 0),
            "cached_input_tokens": int(args.get("cached_input_tokens") or 0),
            "output_tokens": int(args.get("output_tokens") or 0),
        },
        "total_cost_usd": args.get("total_cost_usd"),
        "telemetry_source": str(args.get("telemetry_source") or "").strip(),
        "cost_source": str(args.get("cost_source") or "").strip(),
        "warnings": [],
        "raw": {},
    }
    err_message = str(args.get("error") or "").strip()
    if err_message:
        telemetry["warnings"].append(err_message)

    from agent_runner import _record_automation_end

    ok, err = _record_automation_end(
        row_id=session_id,
        returncode=returncode,
        duration_ms=duration_ms,
        telemetry=telemetry,
    )
    return {"ok": bool(ok), "error": err or ""}
