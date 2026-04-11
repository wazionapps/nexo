#!/usr/bin/env python3
"""Tiny CLI to record a hook lifecycle event from a shell hook.

Closes Fase 3 item 7 of NEXO-AUDIT-2026-04-11. Bash hooks can call:

    python3 ~/Documents/_PhpstormProjects/nexo/src/scripts/nexo-hook-record.py \
        --hook session-start --duration-ms 142 --exit $? --session $SID

This script is intentionally minimal so it adds <50ms of latency to the
hook lifecycle. Best-effort: errors are swallowed (the hook itself must
not fail because observability could not write).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(
    os.environ.get(
        "NEXO_CODE",
        str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME),
    )
)
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))


def main() -> int:
    try:
        from hook_observability import main_cli
    except Exception:
        return 0
    return main_cli(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
