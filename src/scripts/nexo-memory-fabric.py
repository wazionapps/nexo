#!/usr/bin/env python3
# nexo: name=memory-fabric
# nexo: description=Refresh transcript search, historical backup diaries, and graph links.
# nexo: runtime=python
# nexo: cron_id=memory-fabric
# nexo: schedule=02:35
# nexo: recovery_policy=catchup
# nexo: run_on_boot=true
# nexo: run_on_wake=true
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def main() -> int:
    import memory_fabric

    result = memory_fabric.repair_memory_fabric(
        transcript_limit=_int_env("NEXO_MEMORY_FABRIC_TRANSCRIPT_LIMIT", 1000),
        backup_limit=_int_env("NEXO_MEMORY_FABRIC_BACKUP_LIMIT", 10000),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
