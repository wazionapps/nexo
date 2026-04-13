"""Regression tests for Deep Sleep synthesis fallbacks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SYNTHESIZE_PATH = ROOT / "src" / "scripts" / "deep-sleep" / "synthesize.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("test_deep_sleep_synthesize_module", SYNTHESIZE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_synthesize_accepts_nested_direct_write(monkeypatch, tmp_path):
    module = _load_module()

    deep_sleep_dir = tmp_path / "operations" / "deep-sleep"
    run_dir = deep_sleep_dir / "2026-04-05"
    run_dir.mkdir(parents=True)

    (deep_sleep_dir / "2026-04-05-extractions.json").write_text(
        json.dumps({"total_findings": 3, "sessions_analyzed": 2}),
        encoding="utf-8",
    )
    (deep_sleep_dir / "2026-04-05-context.txt").write_text("context", encoding="utf-8")
    (run_dir / "long-horizon-context.json").write_text("{}", encoding="utf-8")

    nested_output = {
        "date": "2026-04-05",
        "actions": [{"type": "learning_add", "text": "Recovered from nested synthesis path"}],
        "cross_session_patterns": [],
        "morning_agenda": [],
        "context_packets": [],
        "summary": "ok",
    }
    (run_dir / "synthesis.json").write_text(json.dumps(nested_output), encoding="utf-8")

    monkeypatch.setattr(module, "DEEP_SLEEP_DIR", deep_sleep_dir)
    monkeypatch.setattr(module, "collect_skill_runtime_candidates", lambda _date: (run_dir / "runtime.json", {"scriptable": [], "improvements": []}))
    monkeypatch.setattr(
        module,
        "run_automation_prompt",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Síntesis completada y escrita en `/tmp/fake/synthesis.json`.\n\nResumen en texto, no JSON.",
            stderr="",
        ),
    )

    monkeypatch.setattr(sys, "argv", ["synthesize.py", "2026-04-05"])
    module.main()

    output_file = deep_sleep_dir / "2026-04-05-synthesis.json"
    assert output_file.exists()
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["actions"][0]["text"] == "Recovered from nested synthesis path"


def test_backfill_engineering_actions_adds_fix_followup():
    module = _load_module()
    payload = {
        "date": "2026-04-05",
        "cross_session_patterns": [
            {
                "pattern": "Releases often need a same-day hotfix",
                "sessions": ["one", "two"],
                "severity": "high",
                "evidence": [{"type": "transcript", "quote": "hotfix again"}],
                "proposed_fix": {
                    "title": "Add pre-release validation script",
                    "description": "Add a pre-release validation script that runs doctor, parity, tests, and publish checks before cutting a release.",
                    "deliverable": "script",
                    "confidence": 0.91,
                },
            }
        ],
        "actions": [],
    }

    result = module.backfill_engineering_actions(payload)

    assert len(result["actions"]) == 1
    action = result["actions"][0]
    assert action["action_type"] == "followup_create"
    assert action["action_class"] == "auto_apply"
    assert action["content"]["description"].startswith("Add a pre-release validation script")
    assert action["dedupe_key"].startswith("engineering-fix:")
