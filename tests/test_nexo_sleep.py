"""Regression tests for sleep/nightly reliability contracts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SLEEP_PATH = ROOT / "src" / "scripts" / "nexo-sleep.py"


def _load_sleep_module(monkeypatch, tmp_path: Path):
    nexo_home = tmp_path / "nexo-home"
    (nexo_home / "runtime" / "coordination").mkdir(parents=True, exist_ok=True)
    (nexo_home / "runtime" / "operations").mkdir(parents=True, exist_ok=True)
    (nexo_home / "runtime" / "memory").mkdir(parents=True, exist_ok=True)
    (nexo_home / "personal" / "brain").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(ROOT / "src"))

    for name in ("nexo_sleep_test_module", "paths"):
        sys.modules.pop(name, None)

    spec = importlib.util.spec_from_file_location("nexo_sleep_test_module", SLEEP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _state_with_learnings(count: int, content_size: int = 10) -> dict:
    return {
        "learnings": [
            {
                "id": i,
                "title": f"Learning {i}",
                "content": "x" * content_size,
                "category": "test",
                "created_at": "2026-06-03",
            }
            for i in range(1, count + 1)
        ],
        "preferences": [],
        "memory_md_lines": 0,
        "claude_mem_old": 0,
        "feedback_count": 0,
    }


def test_dream_writes_full_learning_dump_without_prompt_truncation(monkeypatch, tmp_path):
    sleep = _load_sleep_module(monkeypatch, tmp_path)
    captured = {}

    monkeypatch.setattr(sleep, "LEARNING_CHUNK_MAX_CHARS", 900)

    def fake_render_core_prompt(_name, **kwargs):
        captured["tasks_block"] = kwargs["tasks_block"]
        return kwargs["tasks_block"]

    monkeypatch.setattr(sleep, "render_core_prompt", fake_render_core_prompt)
    monkeypatch.setattr(
        sleep,
        "run_automation_prompt",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    state = _state_with_learnings(80, content_size=500)
    result = sleep.dream(state)

    dump = json.loads(sleep.LEARNINGS_DUMP_FILE.read_text(encoding="utf-8"))
    assert len(dump["learnings"]) == 80
    assert dump["learnings"][-1]["id"] == 80
    assert result["coverage"]["coverage_pct"] == 100.0
    assert len(result["learning_context"]["chunk_files"]) > 1
    assert str(sleep.LEARNINGS_DUMP_FILE) in captured["tasks_block"]
    assert "... (truncated)" not in captured["tasks_block"]


def test_validate_actions_coverage_fails_closed_when_missing_or_partial(monkeypatch, tmp_path):
    sleep = _load_sleep_module(monkeypatch, tmp_path)
    state = _state_with_learnings(100)

    ok, reason = sleep.validate_actions_coverage({}, state)
    assert ok is False
    assert "missing coverage" in reason

    ok, reason = sleep.validate_actions_coverage(
        {
            "coverage": {
                "learnings_visible_count": 41,
                "learnings_total_declared": 100,
                "coverage_pct": 41.0,
            }
        },
        state,
    )
    assert ok is False
    assert "41/100" in reason

    ok, reason = sleep.validate_actions_coverage(
        {
            "coverage": {
                "learnings_visible_count": 100,
                "learnings_total_declared": 100,
                "coverage_pct": 100.0,
            }
        },
        state,
    )
    assert ok is True
    assert reason == "coverage ok"


def test_main_does_not_mark_complete_when_stage_b_fails(monkeypatch, tmp_path):
    sleep = _load_sleep_module(monkeypatch, tmp_path)
    state = _state_with_learnings(20)

    monkeypatch.setattr(sleep, "already_ran_today", lambda: False)
    monkeypatch.setattr(sleep, "was_interrupted", lambda: False)
    monkeypatch.setattr(sleep, "stage_a_cleanup", lambda: {"cleaned": True})
    monkeypatch.setattr(sleep, "collect_brain_state", lambda: state)
    monkeypatch.setattr(sleep, "dream", lambda _state: {"error": "timeout"})

    with pytest.raises(SystemExit) as exc:
        sleep.main()

    assert exc.value.code == 1
    assert not sleep.LAST_RUN_FILE.exists()
    assert sleep.LOCK_FILE.exists()

    health = json.loads(sleep.SLEEP_HEALTH_FILE.read_text(encoding="utf-8"))
    assert health["status"] == "failed"
    assert health["error"] == "timeout"
    assert health["last_run_marked_complete"] is False

    sleep_log = json.loads(sleep.SLEEP_LOG.read_text(encoding="utf-8"))
    assert sleep_log[-1]["status"] == "failed"
    assert sleep_log[-1]["marked_complete"] is False


def test_main_refuses_actions_when_learning_coverage_is_low(monkeypatch, tmp_path):
    sleep = _load_sleep_module(monkeypatch, tmp_path)
    state = _state_with_learnings(100)
    actions_file = sleep.COORD_DIR / "sleep-actions.json"
    actions_file.write_text(
        json.dumps(
            {
                "archive_ids": [1],
                "stale_ids": [],
                "coverage": {
                    "learnings_visible_count": 41,
                    "learnings_total_declared": 100,
                    "coverage_pct": 41.0,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sleep, "already_ran_today", lambda: False)
    monkeypatch.setattr(sleep, "was_interrupted", lambda: False)
    monkeypatch.setattr(sleep, "stage_a_cleanup", lambda: {"cleaned": True})
    monkeypatch.setattr(sleep, "collect_brain_state", lambda: state)
    monkeypatch.setattr(sleep, "dream", lambda _state: {"ok": True, "output_len": 2})
    monkeypatch.setattr(
        sleep,
        "execute_dream_actions",
        lambda *_args, **_kwargs: pytest.fail("low-coverage actions must not execute"),
    )

    with pytest.raises(SystemExit) as exc:
        sleep.main()

    assert exc.value.code == 1
    assert not sleep.LAST_RUN_FILE.exists()

    health = json.loads(sleep.SLEEP_HEALTH_FILE.read_text(encoding="utf-8"))
    assert health["status"] == "failed"
    assert "41/100" in health["error"]
