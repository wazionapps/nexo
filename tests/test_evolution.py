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
        "files_changed TEXT, snapshot_ref TEXT, test_result TEXT, impact INTEGER DEFAULT 0, reasoning TEXT NOT NULL)"
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

    def test_load_objective_normalizes_public_contribution_alias(self, evolution_env, monkeypatch):
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

        assert objective["evolution_mode"] == "public_core"


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
    def test_failed_auto_proposal_rolls_back_and_creates_followup(self, evolution_env, monkeypatch):
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
        status, test_result = conn.execute(
            "SELECT status, test_result FROM evolution_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        followup = conn.execute(
            "SELECT id, description FROM followups ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert status == "rolled_back"
        assert "ROLLBACK" in test_result
        assert followup[0].startswith("NF-EVO-L")
        assert "Patch repair.py" in followup[1]
        assert target_file.read_text() == "print('original')\n"


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

    def test_run_short_circuits_into_public_contribution_mode(self, evolution_env, monkeypatch):
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
        called = []
        runner.run_public_contribution_cycle = lambda **kwargs: called.append(kwargs["cycle_num"])
        runner.dry_run_restore_test = lambda: (_ for _ in ()).throw(AssertionError("local restore test should not run"))

        runner.run()

        assert called == [1]

    def test_public_contribution_falls_back_to_peer_review_when_own_draft_pr_is_open(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        config = {
            "enabled": True,
            "mode": "draft_prs",
            "status": "paused_open_pr",
            "github_user": "alice",
            "fork_repo": "alice/nexo",
            "upstream_repo": "wazionapps/nexo",
            "active_pr_number": 41,
        }
        called = []
        marked = []

        runner.load_public_contribution_config = lambda: dict(config)
        runner.can_run_public_contribution = lambda cfg: (False, "an active Draft PR is already open for this machine", dict(config))
        runner.run_public_pr_validation_cycle = lambda **kwargs: called.append(kwargs) or 2
        runner.mark_public_contribution_result = lambda **kwargs: marked.append(kwargs["result"])

        runner.run_public_contribution_cycle(objective={"history": []}, cycle_num=7)

        assert len(called) == 1
        assert called[0]["cycle_num"] == 7
        assert called[0]["config"]["status"] == "paused_open_pr"
        assert marked == []

    def test_public_contribution_run_preserves_active_pr_state_when_recording_result(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        worktree_dir = evolution_env / "public-worktree"
        worktree_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = evolution_env / "operations" / "public-contrib" / "artifact"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        objective = {
            "history": [],
            "total_evolutions": 0,
            "total_proposals_made": 0,
        }
        config = {
            "enabled": True,
            "mode": "draft_prs",
            "status": "active",
            "github_user": "alice",
            "fork_repo": "alice/nexo",
            "upstream_repo": "wazionapps/nexo",
            "machine_id": "machine-1",
        }
        captured = {}

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        runner.load_public_contribution_config = lambda: dict(config)
        runner.can_run_public_contribution = lambda cfg: (True, "", dict(config))
        runner.verify_claude_cli = lambda: True
        runner._prepare_public_worktree = lambda cfg, title_hint="public-core": (worktree_dir, "contrib/machine-1/test-public-core")
        runner._prime_public_git_identity = lambda worktree, cfg: None
        runner.call_public_claude_cli = lambda prompt, cwd: json.dumps({
            "title": "fix: sample public contribution",
            "problem": "A small public bug",
            "summary": "Fix it safely",
            "tests": ["python3 -m py_compile src/maintenance.py"],
            "risks": ["low"],
        })
        runner._changed_public_files = lambda worktree: ["src/maintenance.py"]
        runner._sanitize_public_diff = lambda worktree, changed_files: (True, "")
        runner._run_public_validation = lambda worktree, changed_files: ["python3 -m py_compile src/maintenance.py"]
        runner._public_pr_duplicate_candidate = lambda cfg, title, changed_files: None
        runner._git = lambda cwd, *args, **kwargs: Result()
        runner._create_draft_pr = lambda worktree, cfg, branch_name, summary: ("https://github.com/wazionapps/nexo/pull/88", 88)
        runner._write_public_artifacts = lambda worktree, branch_name, summary: artifact_dir
        runner.mark_active_pr = lambda **kwargs: {
            **config,
            "active_pr_url": kwargs["pr_url"],
            "active_pr_number": kwargs["pr_number"],
            "active_branch": kwargs["branch"],
            "status": "paused_open_pr",
        }
        runner.mark_public_contribution_result = lambda **kwargs: captured.setdefault("config", dict(kwargs["config"]))
        runner.save_objective = lambda obj: captured.setdefault("objective", json.loads(json.dumps(obj)))
        runner._remove_public_worktree = lambda worktree: None

        runner.run_public_contribution_cycle(objective=objective, cycle_num=3)

        assert captured["config"]["active_pr_number"] == 88
        assert captured["config"]["status"] == "paused_open_pr"
        assert captured["objective"]["history"][0]["pr_url"] == "https://github.com/wazionapps/nexo/pull/88"

    def test_list_reviewable_public_prs_filters_out_own_reviewed_or_unsafe_candidates(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        config = {
            "upstream_repo": "wazionapps/nexo",
            "github_user": "alice",
            "active_pr_number": 99,
        }

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        list_payload = [
            {"number": 11, "title": "own", "url": "https://example/11", "isDraft": True, "author": {"login": "alice"}},
            {"number": 12, "title": "reviewed", "url": "https://example/12", "isDraft": True, "author": {"login": "bob"}},
            {"number": 13, "title": "good", "url": "https://example/13", "isDraft": True, "author": {"login": "carol"}},
            {"number": 14, "title": "unsafe", "url": "https://example/14", "isDraft": True, "author": {"login": "dave"}},
            {"number": 15, "title": "not-public", "url": "https://example/15", "isDraft": True, "author": {"login": "erin"}},
        ]
        detail_payloads = {
            12: {
                "number": 12,
                "title": "reviewed",
                "body": "Source: automated public core evolution from an opt-in machine.",
                "url": "https://example/12",
                "isDraft": True,
                "author": {"login": "bob"},
                "reviews": [{"author": {"login": "alice"}, "state": "COMMENTED"}],
                "files": [{"path": "src/reviewed.py"}],
            },
            13: {
                "number": 13,
                "title": "good",
                "body": "Source: automated public core evolution from an opt-in machine.",
                "url": "https://example/13",
                "isDraft": True,
                "author": {"login": "carol"},
                "reviews": [],
                "files": [{"path": "src/good.py"}],
            },
            14: {
                "number": 14,
                "title": "unsafe",
                "body": "Source: automated public core evolution from an opt-in machine.",
                "url": "https://example/14",
                "isDraft": True,
                "author": {"login": "dave"},
                "reviews": [],
                "files": [{"path": "docs/private-not-allowed.md"}],
            },
            15: {
                "number": 15,
                "title": "not-public",
                "body": "Regular contributor PR without opt-in marker.",
                "url": "https://example/15",
                "isDraft": True,
                "author": {"login": "erin"},
                "reviews": [],
                "files": [{"path": "src/other.py"}],
            },
        }

        def fake_gh(*args, **kwargs):
            if args[:2] == ("pr", "list"):
                return Result(stdout=json.dumps(list_payload))
            if args[:2] == ("pr", "view"):
                return Result(stdout=json.dumps(detail_payloads[int(args[2])]))
            if args[:2] == ("pr", "diff"):
                return Result(stdout=f"diff --git a/src/file.py b/src/file.py\n# pr {args[2]}\n")
            raise AssertionError(f"unexpected gh call: {args}")

        monkeypatch.setattr(runner, "_gh", fake_gh)

        candidates = runner._list_reviewable_public_prs(config, limit=3)

        assert [candidate["number"] for candidate in candidates] == [13]
        assert candidates[0]["files_changed"] == ["src/good.py"]
        assert "diff --git" in candidates[0]["diff_text"]

    def test_public_pr_duplicate_candidate_detects_existing_opt_in_pr(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        monkeypatch.setattr(
            runner,
            "_list_reviewable_public_prs",
            lambda config, limit=12: [
                {
                    "number": 77,
                    "title": "fix: tighten protocol scoring for runtime parity",
                    "url": "https://example/77",
                    "files_changed": ["src/doctor/providers/runtime.py"],
                }
            ],
        )

        duplicate = runner._public_pr_duplicate_candidate(
            {"upstream_repo": "wazionapps/nexo", "github_user": "alice"},
            title="fix: tighten protocol scoring for runtime doctor",
            changed_files=["src/doctor/providers/runtime.py"],
        )

        assert duplicate is not None
        assert duplicate["number"] == 77
        assert "src/doctor/providers/runtime.py" in duplicate["shared_files"]
        assert duplicate["score"] >= 0.75

    def test_public_pr_validation_cycle_logs_review_result(self, evolution_env, monkeypatch):
        evolution_cycle, runner = _load_runner_module(monkeypatch, evolution_env)
        objective = {
            "history": [],
            "total_evolutions": 0,
        }
        config = {
            "enabled": True,
            "mode": "draft_prs",
            "status": "paused_open_pr",
            "github_user": "alice",
            "fork_repo": "alice/nexo",
            "upstream_repo": "wazionapps/nexo",
            "active_pr_number": 41,
        }
        candidate = {
            "number": 23,
            "title": "fix: tighten protocol scoring",
            "url": "https://example/23",
            "body": "Source: automated public core evolution from an opt-in machine.",
            "author": {"login": "bob"},
            "files_changed": ["src/plugins/protocol.py"],
            "diff_text": "diff --git a/src/plugins/protocol.py b/src/plugins/protocol.py\n",
        }
        marks = []
        saved = []
        artifact_dir = evolution_env / "operations" / "public-contrib" / "review-pr23"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        runner.verify_claude_cli = lambda: True
        runner._ensure_public_repo_cache = lambda cfg: None
        runner._list_reviewable_public_prs = lambda cfg, limit=3: [dict(candidate)]
        runner.call_public_claude_cli = lambda prompt, cwd: json.dumps({
            "decision": "comment",
            "summary": "Looks correct but keep an eye on edge cases.",
            "body": "Automated peer review: scoped change looks coherent. Please double-check transcript edge cases.",
        })
        runner._submit_public_pr_review = lambda cfg, pr_number, decision, body: "commented_review"
        runner._write_public_review_artifacts = lambda pr_number, candidate, review: artifact_dir
        runner.mark_public_contribution_result = lambda **kwargs: marks.append(kwargs["result"])
        runner.save_objective = lambda obj: saved.append(json.loads(json.dumps(obj)))

        reviewed = runner.run_public_pr_validation_cycle(objective=objective, cycle_num=5, config=config)

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        row = conn.execute(
            "SELECT classification, status, proposal, test_result FROM evolution_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert reviewed == 1
        assert row[0] == "public_review"
        assert row[1] == "commented_review"
        assert "Review PR #23" in row[2]
        assert "comment" in row[3]
        assert marks == ["peer_reviewed:1"]
        assert saved
        assert saved[-1]["history"][0]["mode"] == "public_core_review"
