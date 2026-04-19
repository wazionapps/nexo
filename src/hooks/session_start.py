#!/usr/bin/env python3
"""SessionStart unified handler — runs the shell scripts that were previously
three separate entries in the hook list.

Contract
--------
The v6.0.0 hook manifest (src/hooks/manifest.json) declares ONE handler per
event. This consolidates the three SessionStart shell scripts
(daily-briefing-check.sh, session-start.sh, and the operations/.session-start-ts
timestamp) behind a single Python entry point so both plugin and npm modes
register the same command.

Each subprocess runs with its own bounded timeout; a failure in one does not
cancel the others. A best-effort hook run is recorded for observability.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent
_NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
if str(_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_DIR.parent))

import paths


def _record(duration_ms: int, exit_code: int, summary: str) -> None:
    """Best-effort hook_runs insert. Never raises."""
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "session_start",
            duration_ms=duration_ms,
            exit_code=exit_code,
            summary=summary,
            session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        )
    except Exception:
        pass


def _run_step(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        tail = (result.stdout or result.stderr or "").strip().splitlines()[-1:] or [""]
        return result.returncode, tail[0][:200]
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as exc:
        return 1, f"error: {exc}"[:200]


def main() -> int:
    started = time.time()

    # Step 1: write .session-start-ts so downstream scripts know when this session began.
    ops_dir = paths.operations_dir()
    ops_dir.mkdir(parents=True, exist_ok=True)
    try:
        (ops_dir / ".session-start-ts").write_text(str(int(started)))
    except Exception:
        pass

    exits: list[int] = []
    summaries: list[str] = []

    # Step 2: daily briefing check
    briefing = _DIR / "daily-briefing-check.sh"
    if briefing.is_file():
        rc, out = _run_step(["bash", str(briefing)], timeout=5)
        exits.append(rc)
        summaries.append(f"briefing:{rc}")

    # Step 3: session-start (the heavy context loader)
    session_start = _DIR / "session-start.sh"
    if session_start.is_file():
        rc, out = _run_step(["bash", str(session_start)], timeout=35)
        exits.append(rc)
        summaries.append(f"session:{rc}")

    final_exit = max(exits) if exits else 0
    duration_ms = int((time.time() - started) * 1000)
    _record(duration_ms, final_exit, " ".join(summaries) or "noop")
    return 0  # never block the session


if __name__ == "__main__":
    sys.exit(main())
