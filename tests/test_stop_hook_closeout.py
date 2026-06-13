from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_stop_scan_blocks_future_commitment_without_followup():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"role": "assistant", "text": "Lo dejo como seguimiento porque está bloqueado por auth."}),
    ])

    assert result["ok"] is False
    assert result["missing_followups"] == 1


def test_stop_scan_accepts_commitment_with_followup_create():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"role": "assistant", "text": "Queda pendiente revisar el deploy."}),
        json.dumps({"tool": "nexo_followup_create", "input": {"id": "NF-DEPLOY"}}),
    ])

    assert result["ok"] is True
    assert result["followup_creates"] == 1


def test_stop_scan_counts_partial_task_close_as_followup_required():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"tool": "nexo_task_close", "tool_input": {"outcome": "partial"}}),
    ])

    assert result["ok"] is False
    assert result["findings"][0]["kind"] == "partial_task_close"
