"""Tests for the per-email checkpoint system in src/scripts/nexo-email-monitor.py.

The 7.9.31 zombie cleanup ran into 4 emails of Francisco/Maria from
2026-04-24 that NEXO had marked ``processed`` but never sent a reply for.
The 24-hour ``_recover_unreplied_processed`` window had already lapsed by
the time anyone noticed, and the workers that originally tried them had
been killed mid-flight by a cascade of Brain releases. 7.9.32 widens
the recovery window to 7 days AND lets the next attempt continue from
where the previous attempt died: per-email checkpoints capture the files
the previous attempt touched and the last assistant text it produced.

These tests pin the helpers at the unit level. The script itself imports
several Brain runtime modules (paths, runtime_home, agent_runner, etc.)
that are not part of a clean test environment, so we load the source by
file and stub out the imports we do not need.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "src" / "scripts" / "nexo-email-monitor.py"


@pytest.fixture
def monitor_module(monkeypatch, tmp_path):
    """Load nexo-email-monitor.py as a module with its runtime dependencies
    stubbed and ``BASE_DIR``/``CHECKPOINTS_DIR``/``WORKER_JOBS_DIR``
    pointing at tmp paths so tests cannot disturb the real config.
    """
    nexo_home = tmp_path / "nexo"
    base_dir = nexo_home / "nexo-email"
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    # Stub Brain modules the script imports at the top so the loader does
    # not pull the whole runtime in for a unit test.
    fake_modules = {
        "automation_controls": MagicMock(),
        "paths": MagicMock(brain_dir=lambda: nexo_home / "brain", nexo_email_dir=lambda: base_dir),
        "runtime_home": MagicMock(export_resolved_nexo_home=lambda *a, **kw: nexo_home),
        "agent_runner": MagicMock(
            AutomationBackendUnavailableError=Exception,
            run_automation_prompt=MagicMock(),
        ),
        "client_preferences": MagicMock(),
        "core_prompts": MagicMock(render_core_prompt=lambda *a, **kw: ""),
        "calibration_runtime": MagicMock(get_operator_profile=lambda: {}),
        "operator_extra_instructions": MagicMock(format_operator_extra_instructions_block=lambda *a, **kw: ""),
        "send_reply_locator": MagicMock(get_send_reply_script_path=lambda *a, **kw: ""),
        "automation_session_locks": MagicMock(),
        "email_config": MagicMock(),
        "hot_context_recall": MagicMock(read_recent_hot_context=lambda *a, **kw: ""),
        "nexo_helper": MagicMock(),
        "script_runtime": MagicMock(get_script_runtime_contract=lambda name: {"available": True}),
    }
    for name, mock in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, mock)

    spec = importlib.util.spec_from_file_location("nem_under_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"nexo-email-monitor.py could not load with stubbed imports: {exc}")

    # Force the dirs onto tmp_path so the helpers operate in isolation.
    cps = base_dir / "checkpoints"
    cps.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "CHECKPOINTS_DIR", cps)
    return module


def test_write_creates_checkpoint_file(monitor_module):
    monitor_module._email_checkpoint_write(
        message_id="<msg-1@example.com>",
        subject="Drafting presentation",
        files_touched=["/tmp/draft.pptx"],
        last_assistant_text="I was halfway through slide 3",
        last_error="exit 137 (oom)",
        attempts=1,
    )
    cp = monitor_module._email_checkpoint_read("<msg-1@example.com>")
    assert cp is not None
    assert cp["subject"] == "Drafting presentation"
    assert cp["attempts"] == 1
    assert cp["files_touched"] == ["/tmp/draft.pptx"]
    assert "halfway through slide 3" in cp["last_assistant_text"]
    assert "oom" in cp["last_error"]


def test_subsequent_attempt_merges_files_touched(monitor_module):
    monitor_module._email_checkpoint_write(
        message_id="<msg-2@example.com>",
        subject="Multi-step task",
        files_touched=["/tmp/a.py"],
        last_assistant_text="first pass",
        last_error="timeout",
        attempts=1,
    )
    monitor_module._email_checkpoint_write(
        message_id="<msg-2@example.com>",
        subject="Multi-step task",
        files_touched=["/tmp/b.py"],
        last_assistant_text="second pass picked up where the first left off",
        last_error="exit 1",
        attempts=2,
    )
    cp = monitor_module._email_checkpoint_read("<msg-2@example.com>")
    assert cp["attempts"] == 2
    assert "/tmp/a.py" in cp["files_touched"]
    assert "/tmp/b.py" in cp["files_touched"]
    # Latest narration wins for last_assistant_text:
    assert "second pass" in cp["last_assistant_text"]
    # first_attempt_at preserved across writes:
    assert cp["first_attempt_at"]


def test_files_touched_is_capped(monitor_module):
    huge = [f"/tmp/file-{i}.txt" for i in range(120)]
    monitor_module._email_checkpoint_write(
        message_id="<msg-cap@example.com>",
        subject="cap",
        files_touched=huge,
        last_assistant_text="",
        last_error="",
        attempts=1,
    )
    cp = monitor_module._email_checkpoint_read("<msg-cap@example.com>")
    assert len(cp["files_touched"]) <= 50


def test_previous_progress_block_renders_human_readable(monitor_module):
    monitor_module._email_checkpoint_write(
        message_id="<msg-3@example.com>",
        subject="Build report for client",
        files_touched=["/tmp/report-v1.md"],
        last_assistant_text="I gathered the metrics, was about to draft section 2",
        last_error="exit 137",
        attempts=2,
    )
    block = monitor_module._build_previous_progress_block(["<msg-3@example.com>"])
    assert "Previous attempt context" in block
    assert "Build report for client" in block
    assert "/tmp/report-v1.md" in block
    assert "draft section 2" in block
    assert "Attempts so far: 2" in block


def test_previous_progress_block_empty_when_no_checkpoints(monitor_module):
    out = monitor_module._build_previous_progress_block(["<unknown@example.com>"])
    assert out == ""


def test_previous_progress_block_handles_empty_or_none_input(monitor_module):
    assert monitor_module._build_previous_progress_block([]) == ""
    assert monitor_module._build_previous_progress_block(None) == ""


def test_delete_removes_file(monitor_module):
    monitor_module._email_checkpoint_write(
        message_id="<msg-del@example.com>",
        subject="x",
        files_touched=[],
        last_assistant_text="",
        last_error="",
        attempts=1,
    )
    assert monitor_module._email_checkpoint_read("<msg-del@example.com>") is not None
    monitor_module._email_checkpoint_delete("<msg-del@example.com>")
    assert monitor_module._email_checkpoint_read("<msg-del@example.com>") is None
    # Idempotent — calling delete on an already-gone checkpoint must not raise.
    monitor_module._email_checkpoint_delete("<msg-del@example.com>")


def test_cleanup_removes_only_old_files(monitor_module, tmp_path):
    # Recent checkpoint: keep
    monitor_module._email_checkpoint_write(
        message_id="<recent@example.com>",
        subject="recent",
        files_touched=[],
        last_assistant_text="",
        last_error="",
        attempts=1,
    )
    # Old checkpoint: backdate mtime 10 days
    monitor_module._email_checkpoint_write(
        message_id="<old@example.com>",
        subject="old",
        files_touched=[],
        last_assistant_text="",
        last_error="",
        attempts=1,
    )
    old_path = monitor_module._email_checkpoint_path("<old@example.com>")
    backdate = time.time() - (10 * 86400)
    os.utime(old_path, (backdate, backdate))

    removed = monitor_module._email_checkpoint_cleanup(max_age_days=7)
    assert removed == 1
    assert not old_path.exists()
    assert monitor_module._email_checkpoint_read("<recent@example.com>") is not None


def test_extract_last_assistant_text_from_json_result(monitor_module):
    out = monitor_module._extract_last_assistant_text_from_run(
        '{"result": "I drafted the reply but the bridge died before sending"}'
    )
    assert out == "I drafted the reply but the bridge died before sending"


def test_extract_last_assistant_text_from_plain_text(monitor_module):
    out = monitor_module._extract_last_assistant_text_from_run("not json at all")
    assert out == "not json at all"


def test_extract_last_assistant_text_handles_empty(monitor_module):
    assert monitor_module._extract_last_assistant_text_from_run("") == ""


def test_extract_last_assistant_text_truncates_long(monitor_module):
    long_text = "x" * 10000
    out = monitor_module._extract_last_assistant_text_from_run(json.dumps({"result": long_text}))
    assert len(out) <= 4000


def test_checkpoint_path_is_filesystem_safe(monitor_module):
    # Message-IDs with angle brackets, @, dots — all of these mix badly
    # with filesystems on macOS. We hash them, so the path must contain
    # only hex.
    p = monitor_module._email_checkpoint_path("<weird/path:in@id.com>")
    assert all(c in "0123456789abcdef" for c in p.stem)


def test_read_returns_none_for_unknown(monitor_module):
    assert monitor_module._email_checkpoint_read("<does-not-exist@example.com>") is None


def test_read_returns_none_for_empty_message_id(monitor_module):
    assert monitor_module._email_checkpoint_read("") is None
