#!/usr/bin/env python3
"""Stop unified handler — delegates to session-stop.sh.

The session-stop shell script is the postmortem writer (diary, buffer flush,
followups). Keeping it as a subprocess lets us ship the new .py handler name
without rewriting ~200 lines of working bash.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent


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


def main() -> int:
    started = time.time()
    script = _DIR / "session-stop.sh"
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
