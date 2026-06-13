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

FUTURE_COMMITMENT_MARKERS = (
    "lo dejo como seguimiento",
    "cuando quieras",
    "pendiente",
    "lo cojo aparte",
    "después",
    "despues",
    "bloqueado por auth",
)
FOLLOWUP_CREATE_MARKERS = ("nexo_followup_create", "mcp__nexo__nexo_followup_create")
PARTIAL_TASK_CLOSE_RE = re.compile(
    r"(nexo_task_close|mcp__nexo__nexo_task_close).{0,800}['\"]?outcome['\"]?\s*[:=]\s*['\"]?partial",
    re.IGNORECASE | re.DOTALL,
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


def _line_text(line: str) -> str:
    try:
        payload = json.loads(line)
    except Exception:
        return line
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(payload)


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
    result = scan_closeout_followup_gaps(lines)
    result["sources"] = sources
    return result


def main() -> int:
    started = time.time()
    script = _DIR / "session-stop.sh"
    exit_code = 0
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
