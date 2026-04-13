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


class TestEvolutionStatus:
    def test_status_falls_back_to_objective_when_metrics_are_missing(self, evolution_env, monkeypatch):
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

        assert "objective fallback" in status
        assert "Last evolution: 2026-04-12" in status
        assert "41%" in status


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

    def test_public_contribution_prioritizes_pending_public_port_queue_item(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        worktree_dir = evolution_env / "public-worktree-queued"
        worktree_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = evolution_env / "operations" / "public-contrib" / "queued-artifact"
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
        prompts = []

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
                "Self-audit already fixed a managed guardrail gap locally; port it to public core.",
                "pending_public_port",
                json.dumps(["src/plugins/protocol.py", "tests/test_protocol.py"]),
                json.dumps({"source": "self-audit"}),
            ),
        )
        conn.commit()
        conn.close()

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        runner.load_public_contribution_config = lambda: dict(config)
        runner.can_run_public_contribution = lambda cfg: (True, "", dict(config))
        runner.verify_claude_cli = lambda: True
        runner._prepare_public_worktree = lambda cfg, title_hint="public-core": (worktree_dir, "contrib/machine-1/public-port")
        runner._prime_public_git_identity = lambda worktree, cfg: None
        runner.call_public_claude_cli = lambda prompt, cwd: prompts.append(prompt) or json.dumps({
            "title": "fix: port managed guardrail tightening",
            "problem": "Managed self-audit already found and fixed the gap locally.",
            "summary": "Port the same guardrail tightening to the public core.",
            "tests": ["python3 -m py_compile src/plugins/protocol.py tests/test_protocol.py"],
            "risks": ["low"],
        })
        runner._changed_public_files = lambda worktree: ["src/plugins/protocol.py", "tests/test_protocol.py"]
        runner._sanitize_public_diff = lambda worktree, changed_files: (True, "")
        runner._run_public_validation = lambda worktree, changed_files: ["python3 -m py_compile src/plugins/protocol.py tests/test_protocol.py"]
        runner._public_pr_duplicate_candidate = lambda cfg, title, changed_files: None
        runner._git = lambda cwd, *args, **kwargs: Result()
        runner._create_draft_pr = lambda worktree, cfg, branch_name, summary: ("https://github.com/wazionapps/nexo/pull/91", 91)
        runner._write_public_artifacts = lambda worktree, branch_name, summary: artifact_dir
        runner.mark_active_pr = lambda **kwargs: {
            **config,
            "active_pr_url": kwargs["pr_url"],
            "active_pr_number": kwargs["pr_number"],
            "active_branch": kwargs["branch"],
            "status": "paused_open_pr",
        }
        runner.mark_public_contribution_result = lambda **kwargs: None
        runner.save_objective = lambda obj: None
        runner._remove_public_worktree = lambda worktree: None

        runner.run_public_contribution_cycle(objective=objective, cycle_num=4)

        assert prompts
        assert "PRIORITY PUBLIC-PORT QUEUE ITEM" in prompts[0]
        assert "Port self-audit managed guardrail fix" in prompts[0]

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        row = conn.execute(
            """SELECT status, test_result
               FROM evolution_log
               WHERE classification = 'public_port_queue'
               ORDER BY id DESC
               LIMIT 1"""
        ).fetchone()
        conn.close()

        assert row[0] == "draft_pr_created"
        payload = json.loads(row[1])
        assert payload["pr_url"] == "https://github.com/wazionapps/nexo/pull/91"
        assert payload["ported_via_cycle"] == 4

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
