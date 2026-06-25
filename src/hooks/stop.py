#!/usr/bin/env python3
"""Stop unified handler — delegates to session-stop.sh.

The session-stop shell script is the postmortem writer (diary, buffer flush,
followups). Keeping it as a subprocess lets us ship the new .py handler name
without rewriting ~200 lines of working bash.
"""
from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent

# Specific future-commitment phrases. Bare words like "pendiente" / "después"
# were removed: they appear constantly in ordinary conversation and, read over a
# GLOBAL rolling buffer, blocked closes spuriously. Each marker now expresses a
# real deferral, not an incidental adverb.
FUTURE_COMMITMENT_MARKERS = (
    "lo dejo como seguimiento",
    "lo cojo aparte",
    "bloqueado por auth",
    "queda pendiente de",
    "lo dejo pendiente",
    "lo retomo más tarde",
    "lo retomo mas tarde",
    "lo vemos en otra sesión",
    "lo vemos en otra sesion",
    "te lo dejo para después",
    "te lo dejo para despues",
    "lo dejo para más tarde",
    "lo dejo para mas tarde",
)
FOLLOWUP_CREATE_MARKERS = ("nexo_followup_create", "mcp__nexo__nexo_followup_create")
PARTIAL_TASK_CLOSE_RE = re.compile(
    r"(nexo_task_close|mcp__nexo__nexo_task_close).{0,800}['\"]?outcome['\"]?\s*[:=]\s*['\"]?partial",
    re.IGNORECASE | re.DOTALL,
)
THINKING_BLOCK_ERROR_RE = re.compile(
    r"("
    r"(?:error\s*400|400\s+bad\s+request|invalid_request_error|bad_request).{0,500}"
    r"(?:thinking|redacted_thinking).{0,500}cannot\s+be\s+modified"
    r"|"
    r"(?:thinking|redacted_thinking).{0,500}cannot\s+be\s+modified.{0,500}"
    r"(?:error\s*400|400\s+bad\s+request|invalid_request_error|bad_request)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
THINKING_RECOVERY_MESSAGE = (
    "Sesión bloqueada por un error 400 de bloques `thinking`/`redacted_thinking`. "
    "He guardado un checkpoint y un borrador de diario con el contexto reciente. "
    "Ejecuta `/clear` y dime `continúa` para retomar desde el estado guardado."
)


def _record(duration_ms: int, exit_code: int) -> None:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "stop",
            duration_ms=duration_ms,
            exit_code=exit_code,
            session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        )
    except Exception:
        pass


def _candidate_transcript_paths() -> list[Path]:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import paths  # type: ignore

        candidates = [paths.brain_dir() / "session_buffer.jsonl"]
    except Exception:
        nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        candidates = [
            nexo_home / "personal" / "brain" / "session_buffer.jsonl",
            nexo_home / "brain" / "session_buffer.jsonl",
        ]

    for key in ("NEXO_TRANSCRIPT_PATH", "CLAUDE_TRANSCRIPT_PATH", "TRANSCRIPT_PATH"):
        raw = os.environ.get(key, "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
    return candidates


def _read_recent_lines(path: Path, max_lines: int = 800) -> list[str]:
    try:
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max(1, max_lines):]
    except Exception:
        return []


def _read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {"raw_stdin": raw[:4000]}
    return payload if isinstance(payload, dict) else {}


def _line_session_id(raw_line: str) -> str:
    try:
        payload = json.loads(raw_line)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("session_id", "sid", "claude_session_id", "sessionId"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _scope_to_session(lines: list[str], sid: str) -> list[str]:
    """Keep only buffer lines belonging to ``sid``. ``session_buffer.jsonl`` is a
    GLOBAL rolling log shared by every session/client, so without scoping the
    closeout gate counts *other* sessions' commitments and blocks this close
    spuriously. If the buffer carries no session ids at all we cannot scope and
    fall back to every line (prior behaviour)."""
    if not sid:
        return lines
    tagged = [(raw, _line_session_id(raw)) for raw in lines]
    if not any(s for _, s in tagged):
        return lines
    return [raw for raw, s in tagged if s == sid]


def _current_session_id() -> str:
    for key in ("CLAUDE_SESSION_ID", "NEXO_SID", "NEXO_SESSION_ID"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def _line_text(line: str) -> str:
    try:
        payload = json.loads(line)
    except Exception:
        return line
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(payload)


def _line_role(line: str) -> str:
    try:
        payload = json.loads(line)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("role", "type", "event"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return ""


def _payload_error_text(payload: dict) -> str:
    """Extract likely error-bearing payload text without scanning tool inputs.

    PreToolUse/Stop payloads can contain arbitrary user commands. Restricting
    this to error-shaped keys avoids false positives when the user merely asks
    us to search for the known 400 message.
    """
    if not isinstance(payload, dict):
        return ""
    chunks: list[str] = []
    stack: list[object] = [payload]
    error_keys = {"error", "errors", "message", "exception", "stderr", "systemmessage", "system_message"}
    ignored_keys = {"tool_input", "toolinput", "input", "command", "prompt"}
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                lowered = str(key).lower()
                if lowered in ignored_keys:
                    continue
                if lowered in error_keys and isinstance(value, str):
                    chunks.append(value)
                elif isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return "\n".join(chunks)


def detect_thinking_block_recovery_needed(lines: list[str], payload: dict | None = None) -> dict:
    payload_text = _payload_error_text(payload or {})
    if payload_text and THINKING_BLOCK_ERROR_RE.search(payload_text):
        return {"match": True, "source": "payload", "excerpt": payload_text[:500]}

    for idx, raw_line in enumerate(lines):
        # Operator prompts and task briefs often mention the known bug by name.
        # Those are not recovery signals; actual client/API failures arrive as
        # assistant/system/tool/error records or hook payload fields.
        if _line_role(raw_line) == "user":
            continue
        text = _line_text(raw_line)
        if THINKING_BLOCK_ERROR_RE.search(text):
            return {"match": True, "source": f"transcript:{idx + 1}", "excerpt": text[:500]}
    return {"match": False}


def _persist_thinking_block_recovery(sid: str, lines: list[str], sources: list[str], detection: dict) -> dict:
    if not sid:
        return {"checkpoint": False, "diary": False, "debt": False, "reason": "missing_session_id"}

    result = {"checkpoint": False, "diary": False, "debt": False}
    recent_tail = "\n".join(_line_text(line) for line in lines[-40:])
    evidence = str(detection.get("excerpt") or "")[:1000]
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import create_protocol_debt, get_db, save_checkpoint, upsert_diary_draft  # type: ignore

        save_checkpoint(
            sid=sid,
            task="Recuperación de sesión tras error 400 thinking blocks",
            task_status="blocked",
            active_files="[]",
            current_goal="Retomar la sesión en una conversación limpia tras /clear.",
            decisions_summary="Se detectó el patrón 'thinking/redacted_thinking blocks cannot be modified'.",
            errors_found=evidence or "Error 400 de bloques thinking/redacted_thinking no modificables.",
            reasoning_thread=recent_tail[-4000:],
            next_step="Ejecutar /clear y pedir a Nero que continúe desde el checkpoint guardado.",
        )
        result["checkpoint"] = True

        upsert_diary_draft(
            sid=sid,
            tasks_seen=json.dumps(["thinking_blocks_400_recovery"], ensure_ascii=False),
            change_ids="[]",
            decision_ids="[]",
            last_context_hint=(recent_tail[-1200:] or evidence),
            heartbeat_count=0,
            summary_draft=(
                "Recuperación automática: sesión interrumpida por error 400 de bloques "
                "thinking/redacted_thinking no modificables. Contexto reciente preservado "
                "para reanudar tras /clear."
            ),
        )
        result["diary"] = True

        conn = get_db()
        existing = conn.execute(
            """SELECT id FROM protocol_debt
               WHERE session_id = ? AND debt_type = ? AND status = 'open'
               LIMIT 1""",
            (sid, "thinking_blocks_400_recovery"),
        ).fetchone()
        if not existing:
            create_protocol_debt(
                sid,
                "thinking_blocks_400_recovery",
                severity="error",
                evidence=(
                    "Stop hook detected the OpenAI/Claude client error pattern "
                    "'thinking or redacted_thinking blocks cannot be modified'. "
                    f"sources={sources}; excerpt={evidence[:500]}"
                ),
            )
        result["debt"] = True
    except Exception as exc:
        result["error"] = exc.__class__.__name__
    return result


def scan_closeout_followup_gaps(lines: list[str]) -> dict:
    findings: list[dict] = []
    followup_creates = 0
    for idx, raw_line in enumerate(lines):
        text = _line_text(raw_line)
        lower = text.lower()
        if any(marker in lower for marker in FOLLOWUP_CREATE_MARKERS):
            followup_creates += 1
        for marker in FUTURE_COMMITMENT_MARKERS:
            if marker in lower:
                findings.append({"line": idx + 1, "kind": "future_commitment", "marker": marker})
                break
        if PARTIAL_TASK_CLOSE_RE.search(text):
            findings.append({"line": idx + 1, "kind": "partial_task_close", "marker": "task_close partial"})

    missing = max(0, len(findings) - followup_creates)
    return {
        "ok": missing == 0,
        "findings": findings,
        "followup_creates": followup_creates,
        "missing_followups": missing,
    }


def _closeout_followup_message(result: dict) -> str:
    examples = ", ".join(
        f"{item.get('kind')}:{item.get('marker')}" for item in result.get("findings", [])[:5]
    )
    return (
        "Cierre bloqueado: hay compromisos futuros o cierres parciales sin seguimiento persistente. "
        f"Detectados={len(result.get('findings', []))}; followups_creados={result.get('followup_creates', 0)}; "
        f"faltan={result.get('missing_followups', 0)}. "
        "Crea los `nexo_followup_create(...)` necesarios antes de cerrar. "
        f"Ejemplos: {examples}"
    )


def check_closeout_followups() -> dict:
    lines: list[str] = []
    sources: list[str] = []
    for path in _candidate_transcript_paths():
        chunk = _read_recent_lines(path)
        if chunk:
            lines.extend(chunk)
            sources.append(str(path))
    sid = _current_session_id()
    lines = _scope_to_session(lines, sid)
    result = scan_closeout_followup_gaps(lines)
    result["sources"] = sources
    result["session_scoped"] = bool(sid)
    return result


def check_thinking_block_recovery(payload: dict | None = None) -> dict:
    lines: list[str] = []
    sources: list[str] = []
    for path in _candidate_transcript_paths():
        chunk = _read_recent_lines(path)
        if chunk:
            lines.extend(chunk)
            sources.append(str(path))
    sid = _current_session_id()
    scoped_lines = _scope_to_session(lines, sid)
    detection = detect_thinking_block_recovery_needed(scoped_lines, payload)
    if not detection.get("match"):
        return {"ok": True, "match": False, "sources": sources, "session_scoped": bool(sid)}
    persisted = _persist_thinking_block_recovery(sid, scoped_lines, sources, detection)
    return {
        "ok": False,
        "match": True,
        "sources": sources,
        "session_scoped": bool(sid),
        "persisted": persisted,
        "detection": detection,
    }


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()
    script = _DIR / "session-stop.sh"
    exit_code = 0
    recovery = check_thinking_block_recovery(payload)
    if recovery.get("match"):
        print(json.dumps({"decision": "block", "systemMessage": THINKING_RECOVERY_MESSAGE}, ensure_ascii=False))
        _record(int((time.time() - started) * 1000), 2)
        return 0
    closeout = check_closeout_followups()
    if not closeout.get("ok", True):
        print(json.dumps({"decision": "block", "systemMessage": _closeout_followup_message(closeout)}, ensure_ascii=False))
        _record(int((time.time() - started) * 1000), 2)
        return 0
    if script.is_file():
        try:
            exit_code = subprocess.run(
                ["bash", str(script)], timeout=10, capture_output=True
            ).returncode
        except Exception:
            exit_code = 1
    _record(int((time.time() - started) * 1000), exit_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
