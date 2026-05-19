from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "src" / "cli.py"


def test_memory_observations_cli_process_runs_bounded_cycle(isolated_db):
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "memory-observations",
            "process",
            "--json",
            "--limit",
            "3",
            "--backfill-limit",
            "5",
            "--pending-sla-seconds",
            "60",
        ],
        text=True,
        capture_output=True,
        timeout=20,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert "backfill" in data
    assert "processed" in data
    assert "health" in data
