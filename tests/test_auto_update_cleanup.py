from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_cleanup_retired_runtime_files_removes_legacy_heartbeat_scripts(tmp_path, monkeypatch):
    import auto_update

    runtime_home = tmp_path / "runtime"
    scripts_dir = runtime_home / "scripts"
    hooks_dir = runtime_home / "hooks"
    scripts_dir.mkdir(parents=True)
    hooks_dir.mkdir(parents=True)

    for target in (
        scripts_dir / "heartbeat-enforcement.py",
        scripts_dir / "heartbeat-posttool.sh",
        scripts_dir / "heartbeat-user-msg.sh",
        hooks_dir / "heartbeat-guard.sh",
    ):
        target.write_text("legacy\n")

    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    auto_update._cleanup_retired_runtime_files()

    assert not (scripts_dir / "heartbeat-enforcement.py").exists()
    assert not (scripts_dir / "heartbeat-posttool.sh").exists()
    assert not (scripts_dir / "heartbeat-user-msg.sh").exists()
    assert not (hooks_dir / "heartbeat-guard.sh").exists()
