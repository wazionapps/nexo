"""Tests for evolution objective normalization and managed execution mode."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
EVOLUTION_RUN = REPO_SRC / "scripts" / "nexo-evolution-run.py"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_runner_module(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))

    import evolution_cycle
    import runtime_power
    import public_contribution

    importlib.reload(evolution_cycle)
    importlib.reload(runtime_power)
    importlib.reload(public_contribution)
    sys.modules.pop("nexo_evolution_run_test", None)
    spec = importlib.util.spec_from_file_location("nexo_evolution_run_test", EVOLUTION_RUN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return evolution_cycle, module


@pytest.fixture
def evolution_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for dirname in [
        "brain",
        "data",
        "logs",
        "scripts",
        "plugins",
        "coordination",
        "snapshots",
        "sandbox" / Path("workspace"),
    ]:
        (home / dirname).mkdir(parents=True, exist_ok=True)

    restore_script = home / "scripts" / "nexo-snapshot-restore.sh"
    restore_script.write_text("#!/usr/bin/env bash\nexit 0\n")
    restore_script.chmod(0o755)

    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE learnings (category TEXT, title TEXT, content TEXT, created_at REAL)")
    conn.execute(
        "CREATE TABLE decisions (domain TEXT, decision TEXT, alternatives TEXT, based_on TEXT, confidence REAL, outcome TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE change_log (files TEXT, what_changed TEXT, why TEXT, affects TEXT, risks TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE session_diary (summary TEXT, decisions TEXT, pending TEXT, mental_state TEXT, domain TEXT, user_signals TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE evolution_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT (datetime('now')), "
        "cycle_number INTEGER NOT NULL, dimension TEXT NOT NULL, proposal TEXT NOT NULL, "
        "classification TEXT NOT NULL DEFAULT 'auto', status TEXT DEFAULT 'pending', "
        "files_changed TEXT, snapshot_ref TEXT, test_result TEXT, impact INTEGER DEFAULT 0, "
        "reasoning TEXT NOT NULL, proposal_payload TEXT DEFAULT NULL)"
    )
    conn.execute(
        "CREATE TABLE evolution_metrics ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL, score INTEGER NOT NULL, "
        "measured_at TEXT DEFAULT (datetime('now')), evidence TEXT NOT NULL, delta INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE followups ("
        "id TEXT PRIMARY KEY, date TEXT, description TEXT NOT NULL, verification TEXT DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'PENDING', recurrence TEXT DEFAULT NULL, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL, reasoning TEXT, priority TEXT DEFAULT 'medium')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


class TestEvolutionObjective:
    def test_load_objective_normalizes_legacy_schema(self, evolution_env, monkeypatch):
        legacy = {
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "review_mode": "manual",
            "dimension_targets": {
                "episodic_memory": 80,
                "agi_readiness": 50,
            },
            "cycles_completed": 3,
            "last_cycle": "2026-04-01",
        }
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps(legacy, indent=2))

        evolution_cycle, _ = _load_runner_module(monkeypatch, evolution_env)
        objective = evolution_cycle.load_objective()

        assert objective["evolution_mode"] == "review"
        assert objective["total_evolutions"] == 3
        assert objective["last_evolution"] == "2026-04-01"
        assert objective["dimensions"]["episodic_memory"]["target"] == 80
        assert objective["dimensions"]["agi"]["target"] == 50
        assert "review_mode" not in objective
        assert "cycles_completed" not in objective

    def test_load_objective_normalizes_public_contribution_alias_to_retired(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve public core reliability",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "draft_prs",
            "dimensions": {"autonomy": {"current": 0, "target": 80}},
            "total_evolutions": 0,
        }, indent=2))

        evolution_cycle, _ = _load_runner_module(monkeypatch, evolution_env)
        objective = evolution_cycle.load_objective()

        assert objective["evolution_mode"] == "retired"

    def test_build_evolution_prompt_requests_dimension_scores_and_uses_objective_fallback(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "managed",
            "dimensions": {
                "autonomy": {"current": 37, "target": 80},
            },
            "total_evolutions": 2,
        }, indent=2))

        evolution_cycle, _ = _load_runner_module(monkeypatch, evolution_env)
        objective = evolution_cycle.load_objective()
        prompt = evolution_cycle.build_evolution_prompt({"current_metrics": {}}, objective)

        assert '"dimension_scores": {' in prompt
        assert '"score_evidence": {' in prompt
        assert "Always include all five canonical keys" in prompt
        assert '"autonomy": 37' in prompt


class TestEvolutionModes:
    def test_managed_mode_allows_core_paths_but_auto_does_not(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        core_target = REPO_SRC / "plugins" / "guard.py"

        assert runner.is_safe_path(str(core_target), mode="managed") is True
        assert runner.is_safe_path(str(core_target), mode="auto") is False

    def test_managed_mode_unlocks_tools_modules_but_keeps_kernel_guarded(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="managed") is True
        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="review") is False
        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="auto") is False
        assert runner.is_safe_path(str(REPO_SRC / "server.py"), mode="managed") is False


class TestManagedExecution:
    def test_retired_runner_does_not_execute_or_create_followup(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "managed",
            "dimensions": {"autonomy": {"current": 0, "target": 80}},
            "total_evolutions": 0,
            "consecutive_failures": 0,
        }, indent=2))

        target_file = evolution_env / "scripts" / "repair.py"
        target_file.write_text("print('original')\n")

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        runner.verify_claude_cli = lambda: True
        runner.dry_run_restore_test = lambda: True
        runner.call_claude_cli = lambda prompt: json.dumps({
            "analysis": "Managed mode found a deterministic but invalid auto edit.",
            "patterns": [],
            "proposals": [
                {
                    "classification": "auto",
                    "dimension": "safety",
                    "action": "Patch repair.py",
                    "reasoning": "Test rollback path",
                    "scope": "local",
                    "changes": [
                        {
                            "file": str(target_file),
                            "operation": "replace",
                            "search": "missing-marker",
                            "content": "print('patched')\n",
                        }
                    ],
                }
            ],
        })

        runner.run()

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        evolution_count = conn.execute("SELECT COUNT(*) FROM evolution_log").fetchone()[0]
        followup_count = conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
        conn.close()

        assert evolution_count == 0
        assert followup_count == 0
        assert target_file.read_text() == "print('original')\n"


class TestEvolutionStatus:
    def test_status_reports_retirement_instead_of_objective_metrics(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "managed",
            "dimensions": {
                "episodic_memory": {"current": 41, "target": 80},
                "autonomy": {"current": 52, "target": 80},
            },
            "total_evolutions": 3,
            "last_evolution": "2026-04-12",
        }, indent=2))

        monkeypatch.setenv("NEXO_HOME", str(evolution_env))
        sys.modules.pop("plugins.evolution", None)
        import plugins.evolution as evolution_plugin

        importlib.reload(evolution_plugin)
        monkeypatch.setattr(evolution_plugin, "get_latest_metrics", lambda: {})

        status = evolution_plugin.handle_evolution_status()

        assert "retired" in status
        assert "Deep Sleep" in status
        assert "41%" not in status


class TestPublicContributionExecution:
    def test_sanitize_public_diff_allows_generic_linux_home_literal(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        diff_text = """diff --git a/src/crons/sync.py b/src/crons/sync.py
+# Example Linux root path prefix: /home/
"""
        runner._git = lambda cwd, *args, **kwargs: Result(stdout=diff_text)

        ok, reason = runner._sanitize_public_diff(evolution_env, ["src/crons/sync.py"])

        assert ok is True
        assert reason == ""

    def test_sanitize_public_diff_blocks_user_home_paths(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        diff_text = """diff --git a/src/file.py b/src/file.py
+# leaked path /home/alice/private
"""
        runner._git = lambda cwd, *args, **kwargs: Result(stdout=diff_text)

        ok, reason = runner._sanitize_public_diff(evolution_env, ["src/file.py"])

        assert ok is False
        assert "private path" in reason

    def test_run_is_retired_and_does_not_create_support_ticket(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve public core reliability",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "public_core",
            "dimensions": {"autonomy": {"current": 0, "target": 80}},
            "total_evolutions": 0,
            "consecutive_failures": 0,
        }, indent=2))

        schedule_file = evolution_env / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "timezone": "UTC",
            "auto_update": True,
            "public_contribution": {
                "enabled": True,
                "mode": "draft_prs",
                "status": "active",
                "github_user": "alice",
                "fork_repo": "alice/nexo",
            },
            "processes": {},
        }, indent=2))

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        tickets = []
        runner.run_public_contribution_cycle = lambda **kwargs: (_ for _ in ()).throw(AssertionError("GitHub PR flow must not run"))
        runner.dry_run_restore_test = lambda: True
        runner.get_week_data = lambda db_path: {"learnings": [], "decisions": [], "changes": [], "diaries": []}
        runner.build_evolution_prompt = lambda week_data, objective: "support ticket prompt"
        runner.verify_claude_cli = lambda: True
        runner.call_claude_cli = lambda prompt: json.dumps({
            "analysis": "Evolution found a repeat support-routing gap.",
            "patterns": [],
            "proposals": [
                {
                    "dimension": "self_improvement",
                    "action": "Create a sanitized support ticket instead of a Draft PR",
                    "reasoning": "GitHub publishing is no longer the product channel.",
                    "classification": "auto",
                    "scope": "public",
                    "impact": "high",
                }
            ],
            "dimension_scores": {},
        })
        runner.create_evolution_support_ticket = lambda **kwargs: tickets.append(kwargs) or {
            "success": True,
            "client_message_id": "evolution-cycle:1",
            "sanitized": True,
        }

        runner.run()

        assert tickets == []
        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        count = conn.execute("SELECT COUNT(*) FROM evolution_log").fetchone()[0]
        conn.close()
        assert count == 0

    def test_public_contribution_cycle_marks_retired_without_support_ticket(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        objective = {"history": [], "total_evolutions": 0}
        config = {"enabled": True, "mode": "draft_prs", "status": "active"}
        tickets = []
        marks = []
        saved = []

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.execute(
            """INSERT INTO evolution_log (
                   cycle_number, dimension, proposal, classification, reasoning, status, files_changed, test_result
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                0,
                "public_core",
                "Port self-audit managed guardrail fix",
                "public_port_queue",
                "Self-audit already fixed a managed guardrail gap locally; route it to support.",
                "pending_public_port",
                json.dumps(["src/plugins/protocol.py", "tests/test_protocol.py"]),
                json.dumps({"source": "self-audit"}),
            ),
        )
        conn.commit()
        conn.close()

        runner.load_public_contribution_config = lambda: dict(config)
        runner.create_evolution_support_ticket = lambda **kwargs: tickets.append(kwargs) or {
            "success": True,
            "client_message_id": "evolution-cycle:4",
            "sanitized": True,
        }
        runner.mark_public_contribution_result = lambda **kwargs: marks.append(kwargs["result"])
        runner.save_objective = lambda obj: saved.append(json.loads(json.dumps(obj)))
        runner._git = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GitHub must not be called"))

        runner.run_public_contribution_cycle(objective=objective, cycle_num=4)

        assert tickets == []
        assert marks == ["retired:evolution_removed"]
        assert saved[-1]["history"][0]["mode"] == "retired"

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        row = conn.execute(
            """SELECT status, test_result
               FROM evolution_log
               WHERE classification = 'public_port_queue'
               ORDER BY id DESC
               LIMIT 1"""
        ).fetchone()
        conn.close()

        assert row[0] == "pending_public_port"

    def test_public_contribution_cycle_without_queue_marks_retired_without_github(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        objective = {"history": [], "total_evolutions": 0}
        config = {"enabled": True, "mode": "draft_prs", "status": "active"}
        marks = []
        saved = []

        runner.load_public_contribution_config = lambda: dict(config)
        runner.create_evolution_support_ticket = lambda **kwargs: (_ for _ in ()).throw(AssertionError("No ticket without queued/proposed items"))
        runner.mark_public_contribution_result = lambda **kwargs: marks.append(kwargs["result"])
        runner.save_objective = lambda obj: saved.append(json.loads(json.dumps(obj)))
        runner._git = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GitHub must not be called"))

        runner.run_public_contribution_cycle(objective=objective, cycle_num=5)

        assert marks == ["retired:evolution_removed"]
        assert saved[-1]["history"][0]["mode"] == "retired"

    def test_public_pr_validation_cycle_is_retired_without_github(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        config = {"enabled": True, "mode": "draft_prs", "status": "paused_open_pr"}
        marks = []

        runner._gh = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GitHub must not be called"))
        runner.mark_public_contribution_result = lambda **kwargs: marks.append(kwargs["result"])

        reviewed = runner.run_public_pr_validation_cycle(objective={"history": []}, cycle_num=5, config=config)

        assert reviewed == 0
        assert marks == ["retired:evolution_removed"]


class TestApplyAcceptedProposals:
    """Fase 2 item 1: user-approved proposals must actually be applied.

    Before m38 + _apply_accepted_proposals, calling nexo_evolution_approve
    just flipped status to 'accepted' and the row was never consumed.
    These tests pin the closed loop: the runner reads accepted rows that
    have a proposal_payload and runs them through execute_auto_proposal.
    """

    def _seed_accepted(
        self,
        env: Path,
        *,
        action: str,
        changes: list[dict],
        cycle_number: int = 1,
        dimension: str = "reliability",
        reasoning: str = "user approved",
        payload_override: object = None,
    ) -> int:
        conn = sqlite3.connect(str(env / "data" / "nexo.db"))
        try:
            payload = (
                payload_override
                if payload_override is not None
                else json.dumps({
                    "classification": "propose",
                    "dimension": dimension,
                    "action": action,
                    "reasoning": reasoning,
                    "scope": "local",
                    "changes": changes,
                })
            )
            cur = conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status, proposal_payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cycle_number, dimension, action, "propose", reasoning, "accepted", payload),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def test_apply_accepted_runs_user_approved_proposal_and_marks_applied(
        self, evolution_env, monkeypatch
    ):
        target = evolution_env / "scripts" / "approved_patch.py"
        target.write_text("print('before')\n")

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        runner.dry_run_restore_test = lambda: True

        log_id = self._seed_accepted(
            evolution_env,
            action="Apply user-approved patch",
            changes=[
                {
                    "file": str(target),
                    "operation": "replace",
                    "search": "print('before')\n",
                    "content": "print('after')\n",
                }
            ],
        )

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=3, evolution_mode="managed"
            )
            row = conn.execute(
                "SELECT status, files_changed, test_result FROM evolution_log WHERE id = ?",
                (log_id,),
            ).fetchone()
        finally:
            conn.close()

        assert stats["attempted"] == 1
        assert stats["applied"] == 1
        assert stats["rolled_back"] == 0
        assert stats["blocked"] == 0
        assert stats["skipped"] == 0
        assert stats["failed"] == 0
        assert row["status"] == "applied"
        assert str(target) in (row["files_changed"] or "")
        assert target.read_text() == "print('after')\n"

    def test_apply_accepted_skips_rows_without_payload(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        # Pre-m38 row: payload is NULL. Must NOT be touched.
        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        cur = conn.execute(
            "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
            "reasoning, status, proposal_payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "reliability", "Pre-m38 legacy row", "propose", "legacy", "accepted", None),
        )
        legacy_id = cur.lastrowid
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=3, evolution_mode="managed"
            )
            row = conn.execute(
                "SELECT status FROM evolution_log WHERE id = ?", (legacy_id,)
            ).fetchone()
        finally:
            conn.close()

        assert stats["attempted"] == 0
        assert stats["applied"] == 0
        assert row["status"] == "accepted"  # legacy row untouched

    def test_apply_accepted_marks_skipped_when_payload_invalid_json(
        self, evolution_env, monkeypatch
    ):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        log_id = self._seed_accepted(
            evolution_env,
            action="Bad payload row",
            changes=[],
            payload_override="not-json{{",
        )

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=3, evolution_mode="managed"
            )
            row = conn.execute(
                "SELECT status, test_result FROM evolution_log WHERE id = ?", (log_id,)
            ).fetchone()
        finally:
            conn.close()

        assert stats["skipped"] == 1
        assert stats["applied"] == 0
        assert row["status"] == "skipped"
        assert "Invalid proposal_payload JSON" in (row["test_result"] or "")

    def test_apply_accepted_marks_skipped_when_changes_array_missing(
        self, evolution_env, monkeypatch
    ):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        log_id = self._seed_accepted(
            evolution_env,
            action="No changes row",
            changes=[],  # empty list, payload object will end up with []
        )

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=3, evolution_mode="managed"
            )
            row = conn.execute(
                "SELECT status, test_result FROM evolution_log WHERE id = ?", (log_id,)
            ).fetchone()
        finally:
            conn.close()

        assert stats["skipped"] == 1
        assert row["status"] == "skipped"
        assert "missing or empty changes" in (row["test_result"] or "")

    def test_apply_accepted_rolls_back_failed_proposal_and_creates_followup(
        self, evolution_env, monkeypatch
    ):
        target = evolution_env / "scripts" / "rollback_target.py"
        target.write_text("print('original')\n")

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        runner.dry_run_restore_test = lambda: True

        log_id = self._seed_accepted(
            evolution_env,
            action="Patch that will fail validation",
            changes=[
                {
                    "file": str(target),
                    "operation": "replace",
                    "search": "missing-marker-not-in-file",
                    "content": "print('would-not-apply')\n",
                }
            ],
        )

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=3, evolution_mode="managed"
            )
            row = conn.execute(
                "SELECT status, test_result FROM evolution_log WHERE id = ?",
                (log_id,),
            ).fetchone()
            followup = conn.execute(
                "SELECT id, description FROM followups ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        # apply_change returns BLOCKED for missing search text — execute_auto_proposal
        # raises and rolls back, so the row ends up rolled_back (no actual writes).
        assert stats["attempted"] == 1
        assert stats["applied"] == 0
        assert row["status"] in ("rolled_back", "blocked")
        assert followup is not None
        assert followup["id"].startswith("NF-EVO-L")
        assert target.read_text() == "print('original')\n"

    def test_apply_accepted_respects_max_to_apply_cap(
        self, evolution_env, monkeypatch
    ):
        targets = []
        for i in range(5):
            t = evolution_env / "scripts" / f"capped_{i}.py"
            t.write_text(f"print('original_{i}')\n")
            targets.append(t)

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        runner.dry_run_restore_test = lambda: True

        for i, t in enumerate(targets):
            self._seed_accepted(
                evolution_env,
                action=f"Patch {i}",
                changes=[
                    {
                        "file": str(t),
                        "operation": "replace",
                        "search": f"print('original_{i}')\n",
                        "content": f"print('patched_{i}')\n",
                    }
                ],
            )

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        conn.row_factory = sqlite3.Row
        try:
            stats = runner._apply_accepted_proposals(
                conn, cycle_num=2, max_to_apply=2, evolution_mode="managed"
            )
            applied_rows = conn.execute(
                "SELECT id FROM evolution_log WHERE status = 'applied' ORDER BY id ASC"
            ).fetchall()
            still_accepted = conn.execute(
                "SELECT COUNT(*) FROM evolution_log WHERE status = 'accepted'"
            ).fetchone()[0]
        finally:
            conn.close()

        assert stats["attempted"] == 2
        assert stats["applied"] == 2
        assert len(applied_rows) == 2
        assert still_accepted == 3  # 5 seeded - 2 applied = 3 left for future cycles

    def test_m38_migration_is_idempotent_and_adds_proposal_payload(self):
        """The m38 migration must be safe to re-run on a real schema."""
        from db._schema import _m38_evolution_log_proposal_payload

        # Build a minimal evolution_log table without proposal_payload, like
        # any pre-m38 install would have.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE evolution_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER NOT NULL, "
            "dimension TEXT NOT NULL, proposal TEXT NOT NULL, "
            "classification TEXT NOT NULL DEFAULT 'auto', status TEXT DEFAULT 'pending', "
            "files_changed TEXT, snapshot_ref TEXT, test_result TEXT, "
            "impact INTEGER DEFAULT 0, reasoning TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO evolution_log (cycle_number, dimension, proposal, reasoning) "
            "VALUES (?, ?, ?, ?)",
            (1, "safety", "pre-m38 row", "legacy"),
        )
        conn.commit()

        # Run twice — must not raise.
        _m38_evolution_log_proposal_payload(conn)
        _m38_evolution_log_proposal_payload(conn)

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(evolution_log)")}
        assert "proposal_payload" in cols

        # Pre-existing row preserved with NULL payload.
        row = conn.execute(
            "SELECT proposal, proposal_payload FROM evolution_log WHERE id = 1"
        ).fetchone()
        assert row["proposal"] == "pre-m38 row"
        assert row["proposal_payload"] is None
        conn.close()
