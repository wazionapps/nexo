#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 2: Extract findings from each session using the configured automation backend.

For each session in the context file, sends the extract-prompt.md to Claude
and collects structured findings. Merges all per-session results into
$DATE-extractions.json.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
PROMPT_FILE = Path(__file__).parent / "extract-prompt.md"

if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from constants import AUTOMATION_SUBPROCESS_TIMEOUT
from core_prompts import render_core_prompt
import paths
try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""

def _deep_sleep_dir() -> Path:
    """Resolve the active deep-sleep workspace at call time.

    Tests and post-migration installs can monkeypatch NEXO_HOME after this
    module has been imported, so we must not freeze the path at import time.
    """
    return paths.operations_dir() / "deep-sleep"


# 3h safety net for the Claude CLI subprocess. Prevents zombie processes while
# still leaving enough headroom for legitimate long per-session extractions.
CLAUDE_TIMEOUT = AUTOMATION_SUBPROCESS_TIMEOUT

# Poison detection: a session checkpoint records the number of failed attempts
# across runs. Once it reaches this limit we stop trying to extract findings
# from that session — repeated failures on the same session (deterministic
# JSON parse errors, unreadable transcripts) only burn API credits and stall
# the whole deep-sleep cycle behind the poisoned session. The session is still
# kept in the output (with the error) so synthesize.py can account for it.
MAX_POISON_ATTEMPTS = 3

# Transient error types worth retrying on the next deep-sleep run instead of
# being counted as a poisoned attempt. `overloaded_error` comes from the
# Anthropic API when it is under load and is the cause of the stuck
# deep-sleep between 2026-04-14 and 2026-04-17 — the first attempt hit it,
# the checkpoint flagged it as permanent failure, and later runs kept
# re-processing the same session forever.
TRANSIENT_ERROR_KINDS = {
    "overloaded_error",
    "rate_limit_error",
    "api_error",
    "timeout",
    "signal",
}
REQUIRED_PROTOCOL_SUMMARY_KEYS = ("guard_check", "heartbeat", "change_log")

# Compact few-shot rendered into the prompt on a `json_schema` retry. Keeps
# the placeholder structure intact so the model sees the exact contract that
# `_is_valid_extraction` enforces. Kept as a string to avoid pulling in a
# template engine for a one-shot block.
JSON_SCHEMA_FEWSHOT = (
    "RETRY_HINT: the previous attempt produced JSON that did not match the "
    "Deep Sleep extraction contract. The response must be a SINGLE JSON "
    "object with the following minimum shape (extra keys allowed):\n"
    "{\n"
    '  "session_id": "<exact session id, string>",\n'
    '  "findings": [ { "type": "...", "summary": "...", "evidence": "..." } ],\n'
    '  "protocol_summary": {\n'
    '    "guard_check": { "ran": true|false, "notes": "..." },\n'
    '    "heartbeat":   { "count": 0, "notes": "..." },\n'
    '    "change_log":  { "entries": 0, "notes": "..." }\n'
    "  }\n"
    "}\n"
    "Mandatory: session_id is a non-empty string equal to {{SESSION_ID}}; "
    "findings is a list of objects; protocol_summary contains the three "
    "object keys above. Return ONLY the JSON object, no prose, no fences."
)


def _record_protocol_debt(
    session_id: str,
    *,
    debt_type: str,
    severity: str,
    evidence: str,
) -> None:
    """Best-effort registration of an extraction failure as protocol debt.

    Imported lazily so the extractor still runs in environments where the
    DB layer is unavailable (e.g. partial installs, unit tests). Any error
    inside the debt path is swallowed: we never want a debt-logging issue
    to mask the real extraction failure already being reported.
    """
    try:
        from db._protocol import create_protocol_debt
    except Exception:  # pragma: no cover - best effort
        return
    try:
        create_protocol_debt(
            session_id,
            debt_type,
            severity=severity,
            evidence=evidence[:3500],
        )
    except Exception as exc:  # pragma: no cover - best effort
        print(f"    Warning: could not record protocol_debt: {exc}", file=sys.stderr)


def _classify_cli_result(result) -> tuple[str, str]:
    """Return (kind, short_message) describing a failed automation backend call.

    Kinds:
      - "overloaded_error" / "rate_limit_error" / "api_error"
          Anthropic API transient failure — do not poison the checkpoint.
      - "signal"   Claude CLI killed by external signal (SIGTERM / SIGKILL / exit>=128).
      - "timeout"  Subprocess hit CLAUDE_TIMEOUT — extremely long session.
      - "json_parse" Claude responded, but output wasn't parseable JSON.
      - "unknown"  Fallback.
    """
    rc = getattr(result, "returncode", -1)
    stderr = (getattr(result, "stderr", "") or "")[:800]
    stdout = (getattr(result, "stdout", "") or "")[:800]
    blob = f"{stderr}\n{stdout}".lower()
    if "overloaded" in blob:
        return "overloaded_error", "Anthropic API overloaded"
    if "rate_limit" in blob or "rate-limit" in blob or "429" in blob:
        return "rate_limit_error", "Anthropic rate-limit hit"
    if '"type":"error"' in blob and '"api_error"' in blob:
        return "api_error", "Anthropic API error"
    if rc >= 128:
        return "signal", f"killed by signal (exit {rc})"
    if rc < 0:
        return "signal", f"subprocess terminated (exit {rc})"
    return "unknown", f"exit {rc}"


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from Claude's response, handling markdown fences."""
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[1:end]).strip()

    # Find the outermost JSON object
    brace_start = text.find("{")
    if brace_start < 0:
        return None

    # Find matching closing brace
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:i + 1])
                except json.JSONDecodeError:
                    break
    return None


def _is_valid_extraction(
    parsed: dict,
    *,
    expected_session_id: str | None = None,
) -> bool:
    """Validate the minimum Deep Sleep extraction contract.

    The extractor prompt's real top-level shape is
    ``session_id/findings/protocol_summary`` plus optional richer sections.
    We intentionally validate the live prompt contract rather than an older
    proposal so a syntactically valid but structurally degraded JSON payload
    does not silently count as success.
    """

    if not isinstance(parsed, dict):
        return False
    session_id = parsed.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    if expected_session_id and session_id != expected_session_id:
        return False
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return False
    if any(not isinstance(item, dict) for item in findings):
        return False
    protocol_summary = parsed.get("protocol_summary")
    if not isinstance(protocol_summary, dict):
        return False
    for key in REQUIRED_PROTOCOL_SUMMARY_KEYS:
        if not isinstance(protocol_summary.get(key), dict):
            return False
    for key in ("emotional_timeline", "abandoned_projects", "skill_candidates"):
        if key in parsed and not isinstance(parsed.get(key), list):
            return False
    if "productivity_score" in parsed and not isinstance(parsed.get("productivity_score"), dict):
        return False
    return True


def _write_debug_extract(session_id: str, kind: str, raw_output: str) -> Path:
    debug_file = _deep_sleep_dir() / f"debug-extract-{session_id[:20]}-{kind}.txt"
    debug_file.parent.mkdir(parents=True, exist_ok=True)
    debug_file.write_text((raw_output or "")[:5000])
    return debug_file


def _safe_session_slug(session_id: str) -> str:
    return (
        session_id
        .replace(".jsonl", "")
        .replace(":", "-")
        .replace("/", "-")
    )


def find_session_file(session_id: str, date_dir: Path, session_txt_map: dict[str, str] | None = None) -> Path | None:
    """Find the individual .txt file for a session."""
    if session_txt_map:
        mapped = session_txt_map.get(session_id)
        if mapped:
            candidate = date_dir / mapped
            if candidate.exists():
                return candidate
    if date_dir and date_dir.exists():
        sid_short = _safe_session_slug(session_id)[:20]
        for f in sorted(date_dir.glob("session-*.txt")):
            if sid_short in f.name:
                return f
    return None


def analyze_session(
    session_id: str,
    date_dir: Path,
    shared_context_file: Path | None,
    session_txt_map: dict[str, str] | None = None,
    *,
    prior_error_kind: str = "",
) -> tuple[dict | None, str | None]:
    """Send a session to the automation backend for extraction analysis.

    Returns (parsed_result, error_kind). `error_kind` is only set on failure.
    See `_classify_cli_result` for possible values.

    ``prior_error_kind`` is consumed by the retry path: when the previous
    attempt failed validation with ``json_schema`` we append a few-shot of
    the contract so the model sees the exact shape it must produce instead
    of repeating the same structurally wrong payload.
    """
    session_file = find_session_file(session_id, date_dir, session_txt_map=session_txt_map)
    if not session_file:
        print(f"    No session file found for {session_id}", file=sys.stderr)
        return None, "missing_session_file"

    print(f"    File: {session_file.name} ({session_file.stat().st_size / 1024:.0f} KB)")

    # Build a short prompt — Claude reads the files itself. We point at the
    # slim shared context rather than the full 400+KB dump so the Claude CLI
    # process doesn't have to stream hundreds of kilobytes of followups /
    # learnings into its context window on every per-session extraction.
    shared_ctx_instruction = ""
    if shared_context_file and shared_context_file.exists():
        shared_ctx_instruction = f"\n\nAlso read the shared context (followups, learnings, DB state) at: {shared_context_file}"

    prompt_template = PROMPT_FILE.read_text()
    prompt = prompt_template.replace("{{CONTEXT_FILE}}", str(session_file))
    prompt = prompt.replace("{{SESSION_ID}}", session_id)
    prompt += shared_ctx_instruction
    if prior_error_kind == "json_schema":
        prompt += "\n\n" + JSON_SCHEMA_FEWSHOT.replace("{{SESSION_ID}}", session_id)

    # Bootstrap the subagent with the day's deep-sleep dir as cwd so its
    # default Read allowlist already covers the session transcript, the
    # shared context, and the day's working files. Without this, the CLI
    # subprocess inherits the parent's cwd (often "/") and fails with
    # `cannot_comply` the first time it tries to Read the session file.
    subagent_cwd = str(date_dir) if date_dir and Path(date_dir).exists() else None

    try:
        json_system_prompt = render_core_prompt(
            "deep-sleep-extract-json-output",
            session_id=session_id,
        )

        result = run_automation_prompt(
            prompt,
            caller="deep-sleep/extract",
            cwd=subagent_cwd,
            timeout=CLAUDE_TIMEOUT,
            output_format="text",
            append_system_prompt=json_system_prompt,
            allowed_tools="Read,Grep,Bash",
            bare_mode=False,
        )

        if result.returncode != 0:
            kind, message = _classify_cli_result(result)
            print(f"    Automation backend {kind} (exit {result.returncode}): {message}", file=sys.stderr)
            return None, kind

        # Filter out stop hook contamination (e.g. "Post-mortem completo.")
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if not line.strip().startswith("Post-mortem") and line.strip()
        )
        parsed = extract_json_from_response(output)
        debug_output = output
        parse_failure_kind = "json_parse"

        # Fallback: if Claude returned text instead of JSON, ask a short conversion call
        if not parsed and len(output.strip()) > 50:
            print(f"    Got text instead of JSON ({len(output)} chars). Converting...")
            convert_prompt = render_core_prompt(
                "deep-sleep-extract-json-conversion",
                analysis=output[:8000],
            )
            convert_result = run_automation_prompt(
                convert_prompt,
                caller="deep-sleep/extract",
                cwd=subagent_cwd,
                timeout=120,
                output_format="text",
                append_system_prompt=json_system_prompt,
                bare_mode=False,
            )
            if convert_result.returncode == 0:
                debug_output = convert_result.stdout
                parsed = extract_json_from_response(convert_result.stdout)
                if parsed:
                    print(f"    Conversion succeeded")

        if parsed and not _is_valid_extraction(parsed, expected_session_id=session_id):
            parse_failure_kind = "json_schema"
            debug_output = json.dumps(parsed, indent=2, ensure_ascii=False)
            parsed = None

        if not parsed:
            debug_file = _write_debug_extract(session_id, parse_failure_kind, debug_output)
            print(
                f"    Failed to validate extraction ({parse_failure_kind}). Raw output saved to {debug_file}",
                file=sys.stderr,
            )
            return None, parse_failure_kind

        return parsed, None

    except AutomationBackendUnavailableError as exc:
        print(f"    Automation backend unavailable: {exc}", file=sys.stderr)
        return None, "backend_unavailable"
    except subprocess.TimeoutExpired:
        print(f"    Automation backend timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr)
        return None, "timeout"


def _write_slim_shared_context(full_path: Path) -> Path:
    """Generate (once per run) a slim version of shared-context.txt.

    The full shared context can exceed 400KB — feeding that to every
    per-session extraction means the Claude CLI subprocess spends most of its
    context window on repeated DB metadata instead of the session transcript.
    The slim version keeps the top-level structure + the first ~200 lines so
    the model still has a summary of followups/learnings/diary samples.
    """
    slim_path = full_path.with_suffix(".slim.txt")
    try:
        raw = full_path.read_text(errors="replace")
    except OSError:
        return full_path
    lines = raw.splitlines()
    head = lines[:200]
    header = [
        "# Shared context (slim) — " + full_path.name,
        f"# original_bytes={full_path.stat().st_size} original_lines={len(lines)}",
        f"# trimmed_to=first_{len(head)}_lines",
        "",
    ]
    try:
        slim_path.write_text("\n".join(header + head), encoding="utf-8")
    except OSError:
        return full_path
    return slim_path


def _load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _save_checkpoint(path: Path, payload: dict) -> None:
    try:
        with path.open("w") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"    Warning: could not persist checkpoint {path}: {exc}", file=sys.stderr)


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    deep_sleep_dir = _deep_sleep_dir()

    context_file = deep_sleep_dir / f"{target_date}-context.txt"
    meta_file = deep_sleep_dir / f"{target_date}-meta.json"
    date_dir = deep_sleep_dir / target_date

    if not context_file.exists() and not date_dir.exists():
        print(f"[extract] No context for {target_date}. Run collect.py first.")
        sys.exit(1)

    # Read metadata to get session list
    if meta_file.exists():
        with open(meta_file) as f:
            meta = json.load(f)
        session_files = meta.get("session_files", [])
        session_txt_map = meta.get("session_txt_map", {})
    else:
        # Fallback: parse context file for session IDs
        print("[extract] No meta file found, scanning context for sessions...")
        session_files = []
        session_txt_map = {}
        if context_file.exists():
            for line in context_file.read_text().splitlines():
                if line.startswith("SESSION ") and ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        sid = parts[1].strip()
                        if sid.endswith(".jsonl"):
                            session_files.append(sid)

    if not session_files:
        print(f"[extract] No sessions to analyze for {target_date}.")
        output = {"date": target_date, "sessions_analyzed": 0, "extractions": []}
        output_file = deep_sleep_dir / f"{target_date}-extractions.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[extract] Output: {output_file}")
        return

    # Shared context file (followups, learnings, DB state).
    # Use a slim copy for the per-session prompts so the Claude CLI doesn't
    # re-read the full 400+KB dump for every single session.
    full_shared_context = date_dir / "shared-context.txt" if date_dir.exists() else None
    shared_context_file: Path | None = None
    if full_shared_context and full_shared_context.exists():
        shared_context_file = _write_slim_shared_context(full_shared_context)
        full_kb = full_shared_context.stat().st_size / 1024
        slim_kb = shared_context_file.stat().st_size / 1024
        print(f"[extract] Shared context: {shared_context_file} ({slim_kb:.0f} KB slim, {full_kb:.0f} KB full)")
    else:
        print("[extract] No shared context file")

    print(f"[extract] Phase 2: Analyzing {len(session_files)} sessions for {target_date}")
    print("[extract] Automation backend: schedule-configured")

    # Checkpoint directory: one JSON per session, survives crashes
    checkpoint_dir = date_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    all_extractions = []
    total_findings = 0
    skipped = 0
    poisoned = 0
    # Two attempts is enough: if a session's extraction fails twice, the cause is
    # almost always deterministic (JSON parse, schema violation) rather than transient,
    # so further retries just burn time. Skip and continue instead.
    MAX_RETRIES = 2

    for i, session_id in enumerate(session_files):
        sid_safe = _safe_session_slug(session_id)[:40]
        checkpoint_file = checkpoint_dir / f"{sid_safe}.json"

        cached = _load_checkpoint(checkpoint_file)
        cached_error_count = int((cached or {}).get("error_count", 0))
        cached_last_error_kind = (cached or {}).get("last_error_kind", "")

        # Successful prior checkpoint → reuse as-is
        if cached and not cached.get("error") and cached.get("findings") is not None:
            findings_count = len(cached.get("findings", []))
            total_findings += findings_count
            all_extractions.append(cached)
            skipped += 1
            print(f"[extract] Session {i + 1}/{len(session_files)}: {session_id} (cached, {findings_count} findings)")
            continue

        # Poisoned checkpoint → skip without burning API calls
        if cached_error_count >= MAX_POISON_ATTEMPTS:
            poisoned += 1
            all_extractions.append(cached or {
                "session_id": session_id,
                "findings": [],
                "error": "poisoned",
                "error_count": cached_error_count,
                "last_error_kind": cached_last_error_kind,
            })
            print(
                f"[extract] Session {i + 1}/{len(session_files)}: {session_id} "
                f"(poisoned, {cached_error_count} prior failures — skip)"
            )
            continue

        print(f"[extract] Session {i + 1}/{len(session_files)}: {session_id}")

        # Retry loop within this run
        result = None
        last_error_kind = ""
        for attempt in range(1, MAX_RETRIES + 1):
            result, error_kind = analyze_session(
                session_id,
                date_dir,
                shared_context_file,
                session_txt_map=session_txt_map,
                prior_error_kind=last_error_kind,
            )
            if result:
                break
            last_error_kind = error_kind or "unknown"
            if attempt < MAX_RETRIES:
                print(f"    -> Attempt {attempt}/{MAX_RETRIES} failed ({last_error_kind}), retrying...")

        if result:
            findings_count = len(result.get("findings", []))
            total_findings += findings_count
            # Persist success and reset error_count so transient past failures
            # don't keep counting against the session.
            result.setdefault("session_id", session_id)
            result["error_count"] = 0
            result["last_error_kind"] = ""
            all_extractions.append(result)
            _save_checkpoint(checkpoint_file, result)
            print(f"    -> {findings_count} findings extracted (checkpointed)")
        else:
            # Transient errors (API overloaded, rate-limit, timeout, killed
            # by signal) should NOT increment the poison counter — they're
            # not the session's fault. They also don't persist a fresh
            # checkpoint, so the next deep-sleep run will retry cleanly.
            transient = last_error_kind in TRANSIENT_ERROR_KINDS
            if transient:
                print(f"    -> Transient failure ({last_error_kind}), will retry on next run.")
                all_extractions.append({
                    "session_id": session_id,
                    "findings": [],
                    "error": "transient",
                    "error_count": cached_error_count,
                    "last_error_kind": last_error_kind,
                })
                # Do not touch the checkpoint — the next run gets a clean retry.
                continue

            new_count = cached_error_count + 1
            state = "poisoned" if new_count >= MAX_POISON_ATTEMPTS else "failed"
            print(
                f"    -> Deterministic failure #{new_count}/{MAX_POISON_ATTEMPTS} "
                f"({last_error_kind}); marked as {state}."
            )
            failed_entry = {
                "session_id": session_id,
                "findings": [],
                "error": state,
                "error_count": new_count,
                "last_error_kind": last_error_kind,
            }
            all_extractions.append(failed_entry)
            _save_checkpoint(checkpoint_file, failed_entry)
            # Surface deterministic extractor failures as protocol debt so
            # the aggregate self-audit cannot silently absorb the pattern.
            # Severity escalates once the session is poisoned because by
            # then it stops being a per-run hiccup and becomes a recurring
            # runtime issue worth a louder signal.
            _record_protocol_debt(
                session_id,
                debt_type=f"deep-sleep.extract.{last_error_kind}",
                severity="error" if state == "poisoned" else "warn",
                evidence=(
                    f"date={target_date} state={state} attempts={new_count}/"
                    f"{MAX_POISON_ATTEMPTS} kind={last_error_kind} "
                    f"checkpoint={checkpoint_file}"
                ),
            )
            if state == "poisoned":
                poisoned += 1

    # Merge into output
    output = {
        "date": target_date,
        "sessions_analyzed": len(session_files),
        "sessions_succeeded": len([e for e in all_extractions if not e.get("error")]),
        "sessions_cached": skipped,
        "sessions_poisoned": poisoned,
        "total_findings": total_findings,
        "extractions": all_extractions,
    }

    output_file = deep_sleep_dir / f"{target_date}-extractions.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    fresh_runs = len(session_files) - skipped - poisoned
    print(
        f"\n[extract] Done. {total_findings} findings from {len(session_files)} sessions "
        f"({skipped} cached, {fresh_runs} fresh, {poisoned} poisoned)."
    )
    print(f"[extract] Output: {output_file}")


if __name__ == "__main__":
    main()
