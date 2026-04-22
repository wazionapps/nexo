#!/usr/bin/env python3
"""PreCompact unified handler — delegates to pre-compact.sh.

The real work lives in the existing shell script (session state snapshot,
checkpoint write, diary reminder). This is a thin Python wrapper so the
manifest can reference a single .py handler per event.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent


def _record(duration_ms: int, exit_code: int) -> None:
    """Log a hook_runs row with the resolved NEXO sid.

    v7.8.2 — the raw `CLAUDE_SESSION_ID` env token is not a NEXO sid, so
    storing it in `hook_runs.session_id` made per-session queries useless
    and left the column empty whenever Claude Code did not forward the
    env. `compact_session_resolver.resolve_nexo_sid` walks the same
    rails the shell script uses (sessions → aliases → per-conv sidecar
    → legacy global sidecar) and returns `(nexo_sid, source)`. The raw
    Claude token and the resolution source end up in `metadata` so an
    operator can debug why a given row is still empty.
    """
    try:
        sys.path.insert(0, str(_DIR.parent))
        sys.path.insert(0, str(_DIR))
        import hook_observability  # type: ignore
        from compact_session_resolver import resolve_nexo_sid  # type: ignore
        claude_id = os.environ.get("CLAUDE_SESSION_ID", "")
        nexo_sid, sid_source = resolve_nexo_sid(claude_id)
        hook_observability.record_hook_run(
            "pre_compact",
            duration_ms=duration_ms,
            exit_code=exit_code,
            session_id=nexo_sid,
            metadata={
                "claude_session_id": claude_id,
                "sid_source": sid_source,
            },
        )
    except Exception:
        pass


def main() -> int:
    started = time.time()
    script = _DIR / "pre-compact.sh"
    exit_code = 0
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
