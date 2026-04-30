from __future__ import annotations

import json
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

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    auto_update._cleanup_retired_runtime_files()

    assert not (scripts_dir / "heartbeat-enforcement.py").exists()
    assert not (scripts_dir / "heartbeat-posttool.sh").exists()
    assert not (scripts_dir / "heartbeat-user-msg.sh").exists()
    assert not (hooks_dir / "heartbeat-guard.sh").exists()


def test_cleanup_legacy_email_routing_config_removes_task_profile(tmp_path):
    import auto_update

    runtime_home = tmp_path / "runtime"
    legacy_runtime = runtime_home / "runtime" / "nexo-email"
    legacy_flat = runtime_home / "nexo-email"
    legacy_runtime.mkdir(parents=True)
    legacy_flat.mkdir(parents=True)

    for path in (
        legacy_runtime / "config.json",
        legacy_flat / "config.json",
    ):
        path.write_text(
            json.dumps(
                {
                    "email": "agent@example.com",
                    "automation_task_profile": "deep",
                    "working_dir": "/tmp",
                }
            )
        )

    actions = auto_update._cleanup_legacy_email_routing_config(runtime_home)

    runtime_payload = json.loads((legacy_runtime / "config.json").read_text())
    flat_payload = json.loads((legacy_flat / "config.json").read_text())
    assert "automation_task_profile" not in runtime_payload
    assert "automation_task_profile" not in flat_payload
    assert len(actions) == 2
    assert all(action.startswith("email-routing-cleanup:") for action in actions)
