from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "src" / "cli.py"


def _run_cli(args: list[str], *, input_text: str = "", env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=20,
        env=run_env,
    )


def test_pre_answer_cli_route_accepts_stdin_payload_without_argv_payload(tmp_path):
    usage_db = tmp_path / "usage.db"
    payload = {
        "query": "hola, dime una frase corta",
        "intent": "auto",
        "source": "test-cli",
        "budget_ms": 250,
    }

    result = _run_cli(
        ["pre-answer", "route", "--json", "--payload-stdin"],
        input_text=json.dumps(payload),
        env={"NEXO_LOCAL_CONTEXT_USAGE_DB": str(usage_db)},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["intent"] == "general"
    assert data["should_inject"] is False
    assert data["route_used"] == "brain_pre_answer_router"
    assert data["deadline_ms"] == 250
    assert data["usage_event"]["ok"] is True
    assert usage_db.exists()


def test_pre_answer_cli_route_rejects_invalid_json():
    result = _run_cli(["pre-answer", "route", "--json", "--payload", "{bad"])

    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["error"] == "invalid_payload_json"


def test_pre_answer_cli_payload_file_read_failure_is_explicit(tmp_path):
    missing = tmp_path / "missing.json"
    result = _run_cli(["pre-answer", "route", "--json", "--payload-file", str(missing)])

    assert result.returncode == 3
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["error"] == "payload_file_read_failed"
