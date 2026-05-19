"""CLI contract tests for large Desktop lifecycle payload files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "cli.py"


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = str(ROOT / "src")
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(ROOT),
        env=child_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_lifecycle_record_accepts_payload_file(tmp_path):
    payload = {
        "title": "Large Desktop conversation",
        "is_active": True,
        "transcript_tail": ["user: hola", "assistant: claro"],
        "body": "x" * (10 * 1024),
    }
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_cli(
        "lifecycle",
        "record",
        "--event-id",
        "evt-payload-file",
        "--action",
        "switch",
        "--conversation-id",
        "conv-payload-file",
        "--payload-file",
        str(payload_file),
    )

    assert result.returncode == 0, result.stderr or result.stdout
    body = json.loads(result.stdout)
    assert body["status"] == "processed"

    status = _run_cli("lifecycle", "status", "--event-id", "evt-payload-file")
    assert status.returncode == 0, status.stderr or status.stdout
    stored = json.loads(status.stdout)
    assert stored["payload_snapshot"]["title"] == "Large Desktop conversation"
    assert stored["payload_snapshot"]["body"] == "x" * (10 * 1024)


def test_lifecycle_record_rejects_unreadable_payload_file(tmp_path):
    missing = tmp_path / "missing.json"

    result = _run_cli(
        "lifecycle",
        "record",
        "--event-id",
        "evt-payload-file-missing",
        "--action",
        "switch",
        "--conversation-id",
        "conv-payload-file",
        "--payload-file",
        str(missing),
    )

    assert result.returncode == 3
    body = json.loads(result.stdout)
    assert body["status"] == "rejected"
    assert body["reason"].startswith("payload-file-read-failed:")
