from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_public_scorecard.py"


def _load_module():
    module_name = "build_public_scorecard_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_collect_longitudinal_metrics_reads_protocol_windows(tmp_path):
    module = _load_module()
    db_path = tmp_path / "nexo.db"
    tool_log_dir = tmp_path / "tool-logs"
    tool_log_dir.mkdir()
    (tool_log_dir / "2026-04-06.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-06T10:00:00Z",
                        "session_id": "ext-1",
                        "tool_name": "mcp__nexo__nexo_heartbeat",
                        "tool_input": {"sid": "nexo-1"},
                        "error": None,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-06T10:01:00Z",
                        "session_id": "ext-1",
                        "tool_name": "Bash",
                        "tool_input": {"command": "pytest tests/test_a.py"},
                        "error": None,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-06T10:02:00Z",
                        "session_id": "ext-1",
                        "tool_name": "Bash",
                        "tool_input": {"command": "pytest tests/test_a.py"},
                        "error": None,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-06T10:03:00Z",
                        "session_id": "ext-1",
                        "tool_name": "Bash",
                        "tool_input": {"command": "pytest tests/test_b.py"},
                        "error": None,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-06T10:12:00Z",
                        "session_id": "ext-1",
                        "tool_name": "Bash",
                        "tool_input": {"command": "pytest tests/test_a.py"},
                        "error": None,
                    }
                ),
            ]
        )
        + "\n"
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT,
            goal TEXT,
            files TEXT,
            opened_at TEXT,
            closed_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_cost_usd REAL,
            created_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, status, goal, files, opened_at, closed_at) VALUES ('PT-1', 'failed', 'Ship release', '/repo/a.py', datetime('now', '-2 days'), datetime('now', '-1 days'))"
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, status, goal, files, opened_at, closed_at) VALUES ('PT-2', 'done', 'Ship release', '/repo/a.py', datetime('now', '-12 hours'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO protocol_debt (status, created_at) VALUES ('open', datetime('now', '-1 day'))"
    )
    conn.execute(
        "INSERT INTO automation_runs (total_cost_usd, created_at) VALUES (0.25, datetime('now', '-1 day'))"
    )
    conn.execute(
        "INSERT INTO automation_runs (total_cost_usd, created_at) VALUES (0.15, datetime('now', '-2 hours'))"
    )
    conn.commit()
    conn.close()

    windows = module.collect_longitudinal_metrics(db_path, tool_log_dir)
    first = windows[0]

    assert first["days"] == 30
    assert first["closed_tasks"] == 2
    assert first["task_success_rate_pct"] == 50.0
    assert first["recovery_after_failure_pct"] == 100.0
    assert first["open_protocol_debt"] == 1
    assert first["unnecessary_tool_call_rate_pct"] == 25.0
    assert first["unnecessary_tool_call_detail"] == {"candidate_calls": 4, "probable_duplicate_calls": 1}
    assert first["cost_telemetry_coverage_pct"] == 100.0
    assert first["cost_per_solved_task"] == 0.4


def test_render_markdown_includes_core_sections():
    module = _load_module()
    markdown = module.render_markdown(
        {
            "generated_at": "2026-04-10T12:00:00+00:00",
            "product_story": "NEXO makes the model around your model smarter.",
            "artifacts": {
                "compare_scorecard": "compare/scorecard.json",
                "locomo_summary": "benchmarks/locomo/results/locomo_nexo_summary.json",
            },
            "claim_map": [
                {
                    "claim": "NEXO publishes a measured long-conversation memory result.",
                    "evidence": ["compare/scorecard.json"],
                    "scope_note": "Memory benchmark only.",
                }
            ],
            "benchmarks": {
                "locomo_rag": {"available": True, "overall_f1": 0.58, "overall_recall": 0.74, "open_domain_f1": 0.63, "multi_hop_f1": 0.33, "temporal_f1": 0.32},
                "ablation_suite": {
                    "available": True,
                    "benchmark": "Runtime ablations",
                    "date": "2026-04-06",
                    "modes": [
                        {"label": "Raw model baseline", "task_success_rate_pct": 20.0},
                        {"label": "Full NEXO", "task_success_rate_pct": 100.0, "conditioned_file_protection_pct": 100.0, "resume_recovery_pct": 100.0},
                    ],
                },
            },
            "longitudinal": [{"days": 30, "available": True, "task_success_rate_pct": 75.0, "avg_time_to_close_minutes": 10.0, "recovery_after_failure_pct": 50.0, "open_protocol_debt": 1, "unnecessary_tool_call_rate_pct": 12.5, "cost_per_solved_task": 0.42}],
        }
    )

    assert "# NEXO Compare Scorecard" in markdown
    assert "## Claims you can inspect today" in markdown
    assert "Memory benchmark only." in markdown
    assert "## What this scorecard does not claim" in markdown
    assert "LoCoMo overall F1: 0.58" in markdown
    assert "Ablation / baseline suite" in markdown
    assert "Raw model baseline: success 20.0%" in markdown
    assert "30d: success 75.0%" in markdown
    assert "unnecessary tool 12.5%" in markdown
    assert "cost/solved 0.42 USD" in markdown
    assert "nexo-brain-architecture.png" in markdown
    assert "nexo_remember" in markdown
    assert "## Artifact map" in markdown
