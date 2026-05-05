"""Tests for deep-sleep extract.py checkpoint/poisoning behavior.

These cover the pre-5.8.1 bug where a single `overloaded_error` response
from Anthropic left a checkpoint that was reused forever, permanently
pretending the session had "0 findings" on every subsequent deep-sleep run.
5.8.1 separates transient errors from deterministic ones and limits
deterministic failures to MAX_POISON_ATTEMPTS before skipping.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
EXTRACT_DIR = SRC_DIR / "scripts" / "deep-sleep"


@pytest.fixture
def extract_module(monkeypatch, tmp_path):
    """Import extract.py as an isolated module with a sandboxed NEXO_HOME."""
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    monkeypatch.setenv("NEXO_CODE", str(SRC_DIR.parent))

    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "deep_sleep_extract_under_test",
        EXTRACT_DIR / "extract.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_result(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_classify_overloaded_error(extract_module):
    result = _make_result(
        1,
        stderr='{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    )
    kind, _ = extract_module._classify_cli_result(result)
    assert kind == "overloaded_error"
    assert kind in extract_module.TRANSIENT_ERROR_KINDS


def test_classify_signal_exit(extract_module):
    result = _make_result(143)
    kind, _ = extract_module._classify_cli_result(result)
    assert kind == "signal"
    assert kind in extract_module.TRANSIENT_ERROR_KINDS


def test_classify_rate_limit(extract_module):
    result = _make_result(1, stderr="HTTP 429: rate_limit_error too many requests")
    kind, _ = extract_module._classify_cli_result(result)
    assert kind == "rate_limit_error"


def test_extract_uses_shared_headless_timeout(extract_module):
    from constants import AUTOMATION_SUBPROCESS_TIMEOUT

    assert extract_module.CLAUDE_TIMEOUT == AUTOMATION_SUBPROCESS_TIMEOUT
    assert extract_module.CLAUDE_TIMEOUT == 10800


def test_valid_extraction_accepts_prompt_contract_minimum(extract_module):
    assert extract_module._is_valid_extraction(
        {
            "session_id": "claude_code:test.jsonl",
            "findings": [],
            "protocol_summary": {
                "guard_check": {},
                "heartbeat": {},
                "change_log": {},
            },
        },
        expected_session_id="claude_code:test.jsonl",
    )


def test_valid_extraction_rejects_missing_protocol_summary(extract_module):
    assert not extract_module._is_valid_extraction(
        {
            "session_id": "claude_code:test.jsonl",
            "findings": [],
        },
        expected_session_id="claude_code:test.jsonl",
    )


def test_analyze_session_rejects_schema_invalid_json(extract_module, tmp_path, monkeypatch):
    date_dir = tmp_path / "sessions"
    date_dir.mkdir(parents=True)
    session_id = "claude_code:test-schema.jsonl"
    session_file = date_dir / "session-01-test.txt"
    session_file.write_text("dummy transcript")

    monkeypatch.setattr(extract_module, "render_core_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        extract_module,
        "run_automation_prompt",
        lambda *args, **kwargs: _make_result(
            0,
            stdout=json.dumps(
                {
                    "session_id": session_id,
                    "findings": [],
                }
            ),
        ),
    )

    parsed, error_kind = extract_module.analyze_session(
        session_id,
        date_dir,
        shared_context_file=None,
        session_txt_map={session_id: session_file.name},
    )

    assert parsed is None
    assert error_kind == "json_schema"
    debug_file = extract_module._deep_sleep_dir() / f"debug-extract-{session_id[:20]}-json_schema.txt"
    assert debug_file.exists()


def test_load_checkpoint_returns_none_on_missing(extract_module, tmp_path):
    assert extract_module._load_checkpoint(tmp_path / "nope.json") is None


def test_load_checkpoint_handles_corrupt_file(extract_module, tmp_path):
    path = tmp_path / "c.json"
    path.write_text("not json")
    assert extract_module._load_checkpoint(path) is None


def test_save_and_load_roundtrip(extract_module, tmp_path):
    path = tmp_path / "c.json"
    extract_module._save_checkpoint(path, {"session_id": "s", "findings": []})
    loaded = extract_module._load_checkpoint(path)
    assert loaded == {"session_id": "s", "findings": []}


def test_slim_context_trims_big_file(extract_module, tmp_path):
    full = tmp_path / "shared-context.txt"
    full.write_text("\n".join(f"line {i}" for i in range(5000)))
    slim = extract_module._write_slim_shared_context(full)
    assert slim != full
    assert slim.exists()
    content = slim.read_text()
    assert "original_lines=5000" in content
    assert "line 0" in content
    assert "line 4999" not in content


def test_extract_main_skips_poisoned_session(extract_module, tmp_path, monkeypatch):
    """A session whose checkpoint already has error_count>=MAX_POISON_ATTEMPTS
    must be skipped without calling the automation backend at all."""
    target_date = "2026-04-17"
    nexo_home = Path(extract_module.NEXO_HOME)
    date_dir = nexo_home / "operations" / "deep-sleep" / target_date
    date_dir.mkdir(parents=True)
    (date_dir / "checkpoints").mkdir()

    session_id = "claude_code:aaaa-bbbb-cccc.jsonl"
    (date_dir / "session-01-claude_code-aaaa.txt").write_text("dummy")

    # Meta + context so main() doesn't short-circuit
    meta = {
        "session_files": [session_id],
        "session_txt_map": {session_id: "session-01-claude_code-aaaa.txt"},
    }
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-meta.json").write_text(
        json.dumps(meta)
    )
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-context.txt").write_text("ctx")

    # Pre-poisoned checkpoint
    poisoned = {
        "session_id": session_id,
        "findings": [],
        "error": "poisoned",
        "error_count": extract_module.MAX_POISON_ATTEMPTS,
        "last_error_kind": "json_parse",
    }
    ckpt_path = date_dir / "checkpoints" / "claude_code-aaaa-bbbb-cccc.json"
    ckpt_path.write_text(json.dumps(poisoned))

    # Fail loudly if extract calls the backend — poisoned sessions must skip.
    def _must_not_be_called(*args, **kwargs):  # pragma: no cover - assertion path
        raise AssertionError("Poisoned sessions must not invoke the automation backend")

    monkeypatch.setattr(extract_module, "analyze_session", _must_not_be_called)
    monkeypatch.setattr(sys, "argv", ["extract.py", target_date])

    extract_module.main()

    output_path = nexo_home / "operations" / "deep-sleep" / f"{target_date}-extractions.json"
    output = json.loads(output_path.read_text())
    assert output["sessions_poisoned"] == 1
    assert output["sessions_succeeded"] == 0
    assert output["extractions"][0]["error"] == "poisoned"


def test_extract_main_transient_does_not_poison(extract_module, tmp_path, monkeypatch):
    """A session whose analyze_session returns a TRANSIENT error kind
    (overloaded_error, rate_limit, signal, timeout) MUST NOT write a
    checkpoint with increased error_count. The next run gets a clean retry.
    """
    target_date = "2026-04-17"
    nexo_home = Path(extract_module.NEXO_HOME)
    date_dir = nexo_home / "operations" / "deep-sleep" / target_date
    (date_dir / "checkpoints").mkdir(parents=True)
    session_id = "claude_code:trans-ient-1.jsonl"
    (date_dir / "session-01-trans.txt").write_text("dummy")

    meta = {
        "session_files": [session_id],
        "session_txt_map": {session_id: "session-01-trans.txt"},
    }
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-meta.json").write_text(
        json.dumps(meta)
    )
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-context.txt").write_text("ctx")

    def _returns_overloaded(*args, **kwargs):
        return None, "overloaded_error"

    monkeypatch.setattr(extract_module, "analyze_session", _returns_overloaded)
    monkeypatch.setattr(sys, "argv", ["extract.py", target_date])

    extract_module.main()

    # No checkpoint written for a transient failure — next run is a clean retry.
    ckpt_path = date_dir / "checkpoints" / "claude_code-trans-ient-1.json"
    assert not ckpt_path.exists(), "Transient failures must not persist a checkpoint"

    output = json.loads(
        (nexo_home / "operations" / "deep-sleep" / f"{target_date}-extractions.json").read_text()
    )
    entry = output["extractions"][0]
    assert entry["error"] == "transient"
    assert entry["last_error_kind"] == "overloaded_error"


def test_extract_main_deterministic_increments_counter(extract_module, tmp_path, monkeypatch):
    """A deterministic failure (json_parse, unknown) increments error_count
    and persists a checkpoint so the next run sees the counter."""
    target_date = "2026-04-17"
    nexo_home = Path(extract_module.NEXO_HOME)
    date_dir = nexo_home / "operations" / "deep-sleep" / target_date
    (date_dir / "checkpoints").mkdir(parents=True)
    session_id = "claude_code:deter-m-1.jsonl"
    (date_dir / "session-01-deter.txt").write_text("dummy")

    meta = {
        "session_files": [session_id],
        "session_txt_map": {session_id: "session-01-deter.txt"},
    }
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-meta.json").write_text(
        json.dumps(meta)
    )
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-context.txt").write_text("ctx")

    def _always_fails_parse(*args, **kwargs):
        return None, "json_parse"

    monkeypatch.setattr(extract_module, "analyze_session", _always_fails_parse)
    monkeypatch.setattr(sys, "argv", ["extract.py", target_date])

    extract_module.main()
    ckpt_path = date_dir / "checkpoints" / "claude_code-deter-m-1.json"
    first = json.loads(ckpt_path.read_text())
    assert first["error_count"] == 1
    assert first["last_error_kind"] == "json_parse"
    assert first["error"] == "failed"

    # Second run: counter increments.
    extract_module.main()
    second = json.loads(ckpt_path.read_text())
    assert second["error_count"] == 2

    # Third run: hits MAX_POISON_ATTEMPTS and flips to poisoned.
    extract_module.main()
    third = json.loads(ckpt_path.read_text())
    assert third["error_count"] == extract_module.MAX_POISON_ATTEMPTS
    assert third["error"] == "poisoned"


def test_extract_main_schema_error_counts_as_deterministic(extract_module, tmp_path, monkeypatch):
    target_date = "2026-04-17"
    nexo_home = Path(extract_module.NEXO_HOME)
    date_dir = nexo_home / "operations" / "deep-sleep" / target_date
    (date_dir / "checkpoints").mkdir(parents=True)
    session_id = "claude_code:schema-1.jsonl"
    (date_dir / "session-01-schema.txt").write_text("dummy")

    meta = {
        "session_files": [session_id],
        "session_txt_map": {session_id: "session-01-schema.txt"},
    }
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-meta.json").write_text(
        json.dumps(meta)
    )
    (nexo_home / "operations" / "deep-sleep" / f"{target_date}-context.txt").write_text("ctx")

    monkeypatch.setattr(extract_module, "analyze_session", lambda *args, **kwargs: (None, "json_schema"))
    monkeypatch.setattr(sys, "argv", ["extract.py", target_date])

    extract_module.main()
    ckpt_path = date_dir / "checkpoints" / "claude_code-schema-1.json"
    first = json.loads(ckpt_path.read_text())
    assert first["error_count"] == 1
    assert first["last_error_kind"] == "json_schema"
    assert first["error"] == "failed"
