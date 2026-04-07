"""Tests for Codex discipline debt capture and horizon rollups in daily self-audit."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
SCRIPT_PATH = REPO_SRC / "scripts" / "nexo-daily-self-audit.py"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_self_audit_module():
    module_name = "nexo_daily_self_audit_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def self_audit_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)

    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            debt_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warn',
            evidence TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            resolution TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT
        )"""
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_DB", str(db_path))
    monkeypatch.setenv("NEXO_TEST_DB", str(db_path))
    monkeypatch.setenv("HOME", str(home))
    return home


def test_check_codex_conditioned_file_discipline_creates_protocol_debt(self_audit_env, monkeypatch):
    from doctor.providers import runtime

    module = _load_self_audit_module()
    module.findings.clear()
    monkeypatch.setattr(
        runtime,
        "_recent_codex_conditioned_file_discipline_status",
        lambda: {
            "conditioned_rules": 2,
            "read_without_protocol": 1,
            "write_without_protocol": 1,
            "write_without_guard_ack": 0,
            "samples": [
                {
                    "kind": "read_without_protocol",
                    "file": "/repo/src/plugins/protocol.py",
                    "tool": "exec_command",
                    "session_file": "/tmp/codex-read.jsonl",
                },
                {
                    "kind": "write_without_protocol",
                    "file": "/repo/src/plugins/protocol.py",
                    "tool": "apply_patch",
                    "session_file": "/tmp/codex-write.jsonl",
                },
            ],
        },
    )

    module.check_codex_conditioned_file_discipline()

    assert module.findings
    assert module.findings[-1]["area"] == "codex-discipline"
    assert module.findings[-1]["severity"] == "ERROR"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    rows = conn.execute(
        "SELECT debt_type, severity FROM protocol_debt ORDER BY debt_type ASC"
    ).fetchall()
    conn.close()
    assert rows == [
        ("codex_conditioned_read_without_protocol", "warn"),
        ("codex_conditioned_write_without_protocol", "error"),
    ]


def test_check_codex_conditioned_file_discipline_dedupes_existing_protocol_debt(self_audit_env, monkeypatch):
    from doctor.providers import runtime

    module = _load_self_audit_module()
    module.findings.clear()
    audit_payload = {
        "conditioned_rules": 1,
        "read_without_protocol": 0,
        "write_without_protocol": 0,
        "write_without_guard_ack": 1,
        "samples": [
            {
                "kind": "write_without_guard_ack",
                "file": "/repo/src/plugins/guard.py",
                "tool": "apply_patch",
                "session_file": "/tmp/codex-guard.jsonl",
            }
        ],
    }
    monkeypatch.setattr(runtime, "_recent_codex_conditioned_file_discipline_status", lambda: audit_payload)

    module.check_codex_conditioned_file_discipline()
    module.check_codex_conditioned_file_discipline()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    count = conn.execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE debt_type = 'codex_conditioned_write_without_guard_ack'"
    ).fetchone()[0]
    conn.close()

    assert count == 1


def test_check_codex_conditioned_file_discipline_records_delete_debt(self_audit_env, monkeypatch):
    from doctor.providers import runtime

    module = _load_self_audit_module()
    module.findings.clear()
    audit_payload = {
        "conditioned_rules": 1,
        "read_without_protocol": 0,
        "write_without_protocol": 0,
        "write_without_guard_ack": 0,
        "delete_without_protocol": 1,
        "delete_without_guard_ack": 0,
        "samples": [
            {
                "kind": "delete_without_protocol",
                "file": "/repo/src/plugins/runtime.py",
                "tool": "exec_command",
                "session_file": "/tmp/codex-delete.jsonl",
            }
        ],
    }
    monkeypatch.setattr(runtime, "_recent_codex_conditioned_file_discipline_status", lambda: audit_payload)

    module.check_codex_conditioned_file_discipline()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    row = conn.execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE debt_type = 'codex_conditioned_delete_without_protocol'"
    ).fetchone()
    conn.close()

    assert row == ("codex_conditioned_delete_without_protocol", "error")


def test_check_codex_startup_discipline_creates_protocol_debt(self_audit_env, monkeypatch):
    from doctor.providers import runtime

    module = _load_self_audit_module()
    module.findings.clear()
    monkeypatch.setattr(
        runtime,
        "_recent_codex_session_parity_status",
        lambda: {
            "files": 2,
            "bootstrap_sessions": 1,
            "startup_sessions": 0,
            "heartbeat_sessions": 1,
            "origins": ["codex_cli_rs"],
            "samples": [
                {
                    "file": "/tmp/codex-missing-startup.jsonl",
                    "bootstrap": False,
                    "startup": False,
                    "heartbeat": False,
                    "origin": "codex_cli_rs",
                },
                {
                    "file": "/tmp/codex-missing-heartbeat.jsonl",
                    "bootstrap": True,
                    "startup": True,
                    "heartbeat": False,
                    "origin": "codex_cli_rs",
                },
            ],
        },
    )

    module.check_codex_startup_discipline()

    assert module.findings
    assert module.findings[-1]["area"] == "codex-startup"
    assert module.findings[-1]["severity"] == "ERROR"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    rows = conn.execute(
        "SELECT debt_type, severity FROM protocol_debt ORDER BY debt_type ASC"
    ).fetchall()
    conn.close()
    assert rows == [
        ("codex_session_missing_heartbeat", "warn"),
        ("codex_session_missing_startup", "error"),
    ]


def test_write_horizon_summaries_creates_daily_weekly_monthly_outputs(self_audit_env):
    module = _load_self_audit_module()
    audit_dir = self_audit_env / "logs" / "self-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    older = {
        "timestamp": "2026-04-04T07:00:00",
        "date_label": "2026-04-04",
        "counts": {"error": 1, "warn": 0, "info": 0},
        "findings": [{"severity": "ERROR", "area": "protocol", "msg": "older"}],
    }
    newer = {
        "timestamp": "2026-04-05T07:00:00",
        "date_label": "2026-04-05",
        "counts": {"error": 0, "warn": 2, "info": 0},
        "findings": [
            {"severity": "WARN", "area": "protocol", "msg": "newer-a"},
            {"severity": "WARN", "area": "runtime", "msg": "newer-b"},
        ],
    }
    (audit_dir / "2026-04-04-daily-summary.json").write_text(json.dumps(older))
    (audit_dir / "2026-04-05-daily-summary.json").write_text(json.dumps(newer))

    current = {
        "timestamp": "2026-04-06T07:00:00",
        "date_label": "2026-04-06",
        "counts": {"error": 0, "warn": 1, "info": 1},
        "findings": [
            {"severity": "WARN", "area": "protocol", "msg": "current-a"},
            {"severity": "INFO", "area": "memory", "msg": "current-b"},
        ],
    }

    outputs = module.write_horizon_summaries(current, now=datetime(2026, 4, 6, 7, 0, 0))

    weekly = json.loads(Path(outputs["weekly_file"]).read_text())
    monthly = json.loads(Path(outputs["monthly_file"]).read_text())
    assert Path(outputs["daily_file"]).is_file()
    assert Path(outputs["weekly_latest"]).is_file()
    assert Path(outputs["monthly_latest"]).is_file()
    assert weekly["horizon"] == "weekly"
    assert weekly["source_daily_summaries"] == 3
    assert weekly["counts"] == {"error": 1, "warn": 3, "info": 1}
    assert "protocol" in weekly["repeated_areas"]
    assert monthly["horizon"] == "monthly"
    assert monthly["source_daily_summaries"] == 3


def test_main_returns_zero_and_writes_summary_when_findings_exist(self_audit_env, monkeypatch):
    module = _load_self_audit_module()
    module.findings.clear()
    (self_audit_env / "operations").mkdir(parents=True, exist_ok=True)

    no_op_checks = [
        "check_overdue_reminders",
        "check_overdue_followups",
        "check_uncommitted_changes",
        "check_cron_errors",
        "check_evolution_health",
        "check_disk_space",
        "check_db_size",
        "check_stale_sessions",
        "check_repetition_rate",
        "check_unused_learnings",
        "check_memory_reviews",
        "check_error_memory_loop",
        "check_repair_changes_missing_learning_capture",
        "check_unformalized_mentions",
        "check_automation_opportunities",
        "check_state_watchers",
        "check_learning_contradictions",
        "check_memory_quality_scores",
        "check_codex_startup_discipline",
        "check_codex_conditioned_file_discipline",
        "check_watchdog_registry",
        "check_snapshot_sync",
        "check_restore_activity",
        "check_runtime_preflight",
        "run_watchdog_smoke",
        "check_watchdog_smoke",
        "check_cognitive_health",
    ]
    for name in no_op_checks:
        monkeypatch.setattr(module, name, lambda: None)

    monkeypatch.setattr(module, "check_bad_responses", lambda: module.finding("ERROR", "test", "boom"))
    monkeypatch.setattr(module, "interpret_findings", lambda raw_findings: True)
    monkeypatch.setattr(module, "write_horizon_summaries", lambda summary_payload, now=None: {})

    result = module.main()

    assert result == 0

    summary = json.loads((self_audit_env / "logs" / "self-audit-summary.json").read_text())
    assert summary["counts"] == {"error": 1, "warn": 0, "info": 0}
    assert summary["findings"][0]["msg"] == "boom"

    catchup_state = json.loads((self_audit_env / "operations" / ".catchup-state.json").read_text())
    assert "self-audit" in catchup_state

    log_body = (self_audit_env / "logs" / "self-audit.log").read_text()
    assert "Self-audit completed with findings: 1 errors, 0 warnings, 0 info." in log_body


def test_check_learning_contradictions_creates_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE learnings (
            id INTEGER PRIMARY KEY,
            title TEXT,
            content TEXT,
            applies_to TEXT,
            status TEXT,
            updated_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    now_ts = datetime(2026, 4, 6, 8, 0, 0).timestamp()
    conn.execute(
        "INSERT INTO learnings (id, title, content, applies_to, status, updated_at) VALUES (1, ?, ?, ?, 'active', ?)",
        ("Never edit protocol.py directly", "Never edit protocol.py directly in hotfixes.", "/repo/src/plugins/protocol.py", now_ts),
    )
    conn.execute(
        "INSERT INTO learnings (id, title, content, applies_to, status, updated_at) VALUES (2, ?, ?, ?, 'active', ?)",
        ("Edit protocol.py directly for hotfixes", "Edit protocol.py directly for urgent hotfixes.", "/repo/src/plugins/protocol.py", now_ts),
    )
    conn.commit()
    conn.close()

    module.check_learning_contradictions()

    assert module.findings
    assert module.findings[-1]["area"] == "contradictions"
    assert module.findings[-1]["severity"] == "ERROR"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    followup = conn.execute("SELECT description, priority FROM followups").fetchone()
    conn.close()
    assert "Resolve contradictory active learnings" in followup[0]
    assert "#1" in followup[0]
    assert "#2" in followup[0]
    assert followup[1] == "critical"


def test_check_error_memory_loop_creates_prevention_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            goal TEXT,
            area TEXT,
            files TEXT,
            status TEXT,
            learning_id INTEGER,
            opened_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, goal, area, files, status, learning_id, opened_at) VALUES (?, ?, ?, ?, ?, NULL, datetime('now'))",
        ("PT-1", "Fix workflow drift", "nexo", "/repo/src/plugins/workflow.py", "failed"),
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, goal, area, files, status, learning_id, opened_at) VALUES (?, ?, ?, ?, ?, NULL, datetime('now'))",
        ("PT-2", "Fix workflow drift again", "nexo", "/repo/src/plugins/workflow.py", "blocked"),
    )
    conn.commit()
    conn.close()

    module.check_error_memory_loop()

    assert module.findings
    assert module.findings[-1]["area"] == "prevention"
    assert module.findings[-1]["severity"] == "WARN"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    followup = conn.execute("SELECT description, priority FROM followups").fetchone()
    conn.close()
    assert "repeated failed/blocked protocol tasks around /repo/src/plugins/workflow.py" in followup[0]
    assert followup[1] == "high"


def test_check_repair_changes_missing_learning_capture_creates_debt_and_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()
    module._attempt_repair_learning_auto_capture = lambda row: {"ok": False, "error": "forced failure"}

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            created_at TEXT,
            files TEXT,
            what_changed TEXT,
            why TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            reasoning TEXT,
            prevention TEXT,
            applies_to TEXT,
            status TEXT,
            created_at REAL,
            updated_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO change_log (session_id, created_at, files, what_changed, why)
           VALUES ('sid-1', datetime('now'), ?, ?, ?)""",
        (
            "/repo/src/plugins/protocol.py",
            "Fixed protocol regression in write flow",
            "Repair critical bug before it repeats",
        ),
    )
    conn.commit()
    conn.close()

    module.check_repair_changes_missing_learning_capture()

    assert module.findings
    assert module.findings[-1]["area"] == "learning-capture"
    assert module.findings[-1]["severity"] == "WARN"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    debt = conn.execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE debt_type = 'repair_change_without_learning_capture'"
    ).fetchone()
    followup = conn.execute("SELECT description, priority FROM followups").fetchone()
    conn.close()
    assert debt == ("repair_change_without_learning_capture", "warn")
    assert "Capture canonical learning for repair change touching /repo/src/plugins/protocol.py" in followup[0]
    assert followup[1] == "high"


def test_check_repair_changes_missing_learning_capture_auto_captures_when_runtime_can_create_learning(self_audit_env):
    import importlib
    import db._core as db_core
    import db._schema as db_schema
    import db

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    db.init_db()
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            created_at TEXT,
            files TEXT,
            what_changed TEXT,
            why TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO change_log (session_id, files, what_changed, why, created_at)
           VALUES ('sid-1', ?, ?, ?, datetime('now'))""",
        (
            "/repo/src/plugins/protocol.py",
            "Fixed protocol task close regression",
            "Repair canonical close path before the bug repeats",
        ),
    )
    conn.commit()
    conn.close()

    module.check_repair_changes_missing_learning_capture()

    assert module.findings
    assert module.findings[-1]["area"] == "learning-capture"
    assert module.findings[-1]["severity"] == "INFO"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    learning = conn.execute(
        "SELECT category, title, applies_to, status FROM learnings WHERE category = 'nexo-ops' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    debt = conn.execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE debt_type = 'repair_change_without_learning_capture'"
    ).fetchone()[0]
    conn.close()

    assert learning is not None
    assert learning[0] == "nexo-ops"
    assert "protocol task close regression" in learning[1].lower()
    assert learning[2] == "/repo/src/plugins/protocol.py"
    assert learning[3] == "active"
    assert debt == 0


def test_check_unformalized_mentions_creates_formalization_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            goal TEXT,
            area TEXT,
            learning_id INTEGER,
            followup_id TEXT,
            opened_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, goal, area, learning_id, followup_id, opened_at) VALUES (?, ?, ?, NULL, '', datetime('now'))",
        ("PT-11", "Prepare release readiness checks for v3", "nexo"),
    )
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, goal, area, learning_id, followup_id, opened_at) VALUES (?, ?, ?, NULL, '', datetime('now'))",
        ("PT-12", "Prepare release readiness gates for v3", "nexo"),
    )
    conn.commit()
    conn.close()

    module.check_unformalized_mentions()

    assert module.findings
    assert module.findings[-1]["area"] == "formalization"
    assert module.findings[-1]["severity"] == "WARN"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    followup = conn.execute("SELECT description, priority FROM followups").fetchone()
    conn.close()
    assert "Formalize repeated unresolved theme in nexo" in followup[0]
    assert "Prepare release readiness" in followup[0]
    assert followup[1] == "high"


def test_check_automation_opportunities_creates_opportunity_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            goal TEXT,
            area TEXT,
            files TEXT,
            status TEXT,
            closed_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    rows = [
        ("PT-31", "Refresh release notes and scorecard", "nexo", "/repo/README.md", "done"),
        ("PT-32", "Refresh release notes and benchmarks", "nexo", "/repo/README.md", "done"),
        ("PT-33", "Refresh release notes and docs", "nexo", "/repo/README.md", "done"),
    ]
    conn.executemany(
        "INSERT INTO protocol_tasks (task_id, goal, area, files, status, closed_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        rows,
    )
    conn.commit()
    conn.close()

    module.check_automation_opportunities()

    assert module.findings
    assert module.findings[-1]["area"] == "opportunities"
    assert module.findings[-1]["severity"] == "INFO"

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    followup = conn.execute("SELECT description, priority FROM followups").fetchone()
    conn.close()
    assert "Extract a reusable automation for repeated nexo work" in followup[0]
    assert "Refresh release notes" in followup[0]
    assert followup[1] == "medium"


def test_check_state_watchers_emits_warning(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE state_watchers (
            watcher_id TEXT PRIMARY KEY,
            watcher_type TEXT NOT NULL,
            title TEXT NOT NULL,
            target TEXT DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'active',
            config TEXT DEFAULT '{}',
            last_health TEXT NOT NULL DEFAULT 'unknown',
            last_result TEXT DEFAULT '{}',
            last_checked_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "INSERT INTO state_watchers (watcher_id, watcher_type, title, status, config) VALUES (?, ?, ?, 'active', ?)",
        ("SW-1", "expiry", "SSL cert", json.dumps({"due_at": "2026-04-07", "warn_days": 10, "critical_days": 2})),
    )
    conn.commit()
    conn.close()

    module.check_state_watchers()

    assert module.findings
    assert module.findings[-1]["area"] == "watchers"
    assert module.findings[-1]["severity"] in {"WARN", "ERROR"}


def test_check_memory_quality_scores_creates_followup(self_audit_env):
    module = _load_self_audit_module()
    module.findings.clear()

    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    conn.execute(
        """CREATE TABLE learnings (
            id INTEGER PRIMARY KEY,
            category TEXT,
            title TEXT,
            content TEXT,
            reasoning TEXT,
            prevention TEXT,
            applies_to TEXT,
            status TEXT,
            priority TEXT,
            weight REAL,
            guard_hits INTEGER,
            last_guard_hit_at REAL,
            review_due_at REAL,
            created_at REAL,
            updated_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT,
            verification TEXT,
            status TEXT,
            reasoning TEXT,
            recurrence TEXT,
            created_at REAL,
            updated_at REAL,
            priority TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO learnings (
            id, category, title, content, reasoning, prevention, applies_to, status,
            priority, weight, guard_hits, created_at, updated_at
        ) VALUES (1, 'nexo-ops', 'Weak rule', 'Too short', '', '', '/repo/src/server.py', 'active', 'medium', 0.3, 0, 0, 0)"""
    )
    conn.commit()
    conn.close()

    module.check_memory_quality_scores()

    assert module.findings
    assert module.findings[-1]["area"] == "memory-quality"
    conn = sqlite3.connect(str(self_audit_env / "data" / "nexo.db"))
    followup = conn.execute("SELECT description FROM followups").fetchone()
    conn.close()
    assert "Refresh low-quality conditioned learnings" in followup[0]
