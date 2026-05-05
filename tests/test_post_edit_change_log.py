from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
HOOK_PATH = SRC_DIR / "hooks" / "post_edit_change_log.py"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("post_edit_change_log_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_edit_change_log_records_edit_payload(isolated_db):
    import db

    module = _load_module()
    db.register_session(
        "nexo-1777970000-10001",
        "post edit test",
        external_session_id="claude-post-edit-1",
        session_client="claude_code",
    )

    result = module.record_post_edit_change(
        {
            "session_id": "claude-post-edit-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/example.py"},
        }
    )

    assert result["ok"] is True
    row = db.get_db().execute(
        "SELECT session_id, files, what_changed, triggered_by FROM change_log"
    ).fetchone()
    assert row["session_id"] == "nexo-1777970000-10001"
    assert row["files"] == "/repo/src/example.py"
    assert "Edit" in row["what_changed"]
    assert row["triggered_by"] == "post_edit_change_log.py"


def test_post_edit_change_log_ignores_non_write_tools(isolated_db):
    import db

    module = _load_module()
    result = module.record_post_edit_change(
        {
            "session_id": "nexo-1777970000-10002",
            "tool_name": "Bash",
            "tool_input": {"command": "echo ok"},
        }
    )

    assert result == {"ok": True, "skipped": True, "reason": "tool_not_write"}
    count = db.get_db().execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
    assert count == 0


def test_post_tool_use_invokes_post_edit_recorder():
    post_tool_use = (SRC_DIR / "hooks" / "post_tool_use.py").read_text()
    assert "post_edit_change_log.py" in post_tool_use
