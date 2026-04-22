#!/usr/bin/env python3
"""PostCompact unified handler — delegates to post-compact.sh.

The real work (checkpoint lookup, fail-closed cross-conv guard, Core
Memory Block systemMessage emission, pending-event enqueue) lives in
the shell script. This wrapper runs it, captures its stdout verbatim
(so Claude Code gets the systemMessage JSON), and records an entry in
hook_runs for auditability.

Matches pre_compact.py shape — one .py handler per event so the
manifest can keep a single clean row per hook type.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent


def _record(duration_ms: int, exit_code: int, claude_session_id: str) -> None:
    """Log a hook_runs row with the resolved NEXO sid.

    v7.8.2 — see the matching docstring in pre_compact.py. Post-compact
    runs after the shell script has already consumed the per-conv
    sidecar, but the DB rails (sessions/aliases) stay valid, so the
    resolver still returns a sid in the common case. `sid_source` goes
    into metadata for empty-row triage.
    """
    try:
        sys.path.insert(0, str(_DIR.parent))
        sys.path.insert(0, str(_DIR))
        import hook_observability  # type: ignore
        from compact_session_resolver import resolve_nexo_sid  # type: ignore
        nexo_sid, sid_source = resolve_nexo_sid(claude_session_id)
        hook_observability.record_hook_run(
            "post_compact",
            duration_ms=duration_ms,
            exit_code=exit_code,
            session_id=nexo_sid,
            metadata={
                "claude_session_id": claude_session_id,
                "sid_source": sid_source,
            },
        )
    except Exception:
        pass


def main() -> int:
    started = time.time()
    script = _DIR / "post-compact.sh"
    exit_code = 0
    # Preserve stdout: Claude Code reads the JSON systemMessage line
    # the shell script prints. We proxy it through so the runtime sees
    # exactly what post-compact.sh emits.
    if script.is_file():
        try:
            r = subprocess.run(
                ["bash", str(script)], timeout=15, capture_output=True
            )
            if r.stdout:
                sys.stdout.write(r.stdout.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            exit_code = r.returncode
        except Exception:
            exit_code = 1
    _record(
        int((time.time() - started) * 1000),
        exit_code,
        os.environ.get("CLAUDE_SESSION_ID", ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
