from __future__ import annotations

import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_server_exposes_protocol_runtime_tools(isolated_db):
    import server

    for name in (
        "nexo_cortex_check",
        "nexo_guard_check",
        "nexo_task_open",
        "nexo_task_acknowledge_guard",
        "nexo_task_close",
        "nexo_workflow_open",
        "nexo_workflow_update",
    ):
        assert hasattr(server, name), f"{name} missing from server.py"
        assert callable(getattr(server, name)), f"{name} is not callable"
