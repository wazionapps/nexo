"""Tests for Skills v2 runtime, sync, approvals, and CLI integration."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
CLI_PY = REPO_SRC / "cli.py"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_skill_stack():
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._skills as db_skills
    import db
    import skills_runtime
    import doctor.providers.runtime as doctor_runtime

    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_skills)
    importlib.reload(db)
    importlib.reload(skills_runtime)
    importlib.reload(doctor_runtime)
    return db, skills_runtime, doctor_runtime, db_skills


def _run_cli(nexo_home: Path, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "NEXO_HOME": str(nexo_home),
        "NEXO_CODE": str(REPO_SRC),
    }
    return subprocess.run(
        [sys.executable, str(CLI_PY), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _write_skill_definition(base_dir: Path, slug: str, metadata: dict, guide: str = "# Guide\n") -> Path:
    skill_dir = base_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(metadata)
    script_body = payload.pop("_script_body", None)
    (skill_dir / "skill.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    (skill_dir / "guide.md").write_text(guide)
    executable_entry = payload.get("executable_entry", "")
    if script_body and executable_entry:
        script_path = skill_dir / executable_entry
        script_path.write_text(script_body)
        script_path.chmod(0o755)
    return skill_dir


def _seed_outcome_pattern(
    db,
    *,
    selected_choice: str,
    count: int,
    success: bool = True,
    area: str = "ops",
    task_type: str = "execute",
    goal_profile_id: str = "ops_efficiency",
):
    for idx in range(count):
        outcome = db.create_outcome(
            action_type="manual_review",
            description=f"seed {selected_choice} #{idx}",
            expected_result=f"{selected_choice} should succeed",
            metric_source="manual",
            target_value=1,
            target_operator="gte",
            deadline="2099-01-01T00:00:00" if success else "2000-01-01T00:00:00",
        )
        db.create_cortex_evaluation(
            goal="Seed skill candidate from outcomes",
            task_type=task_type,
            area=area,
            impact_level="high",
            alternatives=[{"name": selected_choice, "description": selected_choice}],
            scores=[{"name": selected_choice, "total_score": 1.0}],
            recommended_choice=selected_choice,
            recommended_reasoning="seed",
            linked_outcome_id=outcome["id"],
            goal_profile_id=goal_profile_id,
            goal_profile_labels=[],
            goal_profile_weights={},
            selected_choice=selected_choice,
            selection_reason="seed",
            selection_source="recommended",
        )
        db.evaluate_outcome(outcome["id"], actual_value=1.0 if success else 0.0)


@pytest.fixture
def skills_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for dirname in [
        "data",
        "scripts",
        "plugins",
        "hooks",
        "coordination",
        "operations",
        "logs",
        "skills",
        "skills-runtime",
    ]:
        (home / dirname).mkdir(parents=True, exist_ok=True)
    (home / "crons").mkdir(parents=True, exist_ok=True)
    (home / "crons" / "manifest.json").write_text(json.dumps({"crons": []}))
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


class TestSkillsRuntime:
    def test_sync_discovers_core_skill(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        result = skills_runtime.sync_skills()

        assert "SK-RUN-RUNTIME-DOCTOR" in result["ids"]
        skill = db.get_skill("SK-RUN-RUNTIME-DOCTOR")
        assert skill is not None
        assert skill["source_kind"] == "core"
        assert skill["mode"] == "execute"
        assert skill["file_path"].startswith(str(skills_env / "skills-runtime"))

    def test_apply_core_skill_dry_run(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        result = skills_runtime.apply_skill(
            "SK-RUN-RUNTIME-DOCTOR",
            params={"tier": "runtime"},
            dry_run=True,
            context="doctor smoke",
        )

        assert result["ok"] is True
        assert result["resolved_mode"] == "execute"
        assert result["resolved_params"]["tier"] == "runtime"
        assert result["script_command"][-1] == "runtime"
        assert result["script_doctor"]["status"] == "pass"

    def test_local_skill_is_auto_approved(self, skills_env):
        db, skills_runtime, doctor_runtime, _ = _reload_skill_stack()
        db.init_db()

        result = db.materialize_personal_skill_definition(
            {
                "id": "SK-LOCAL-EDIT",
                "name": "Local Edit Skill",
                "description": "Edits local files and is auto-approved by the autonomous runtime.",
                "level": "published",
                "mode": "execute",
                "execution_level": "local",
                "approval_required": True,
                "content": "# Local Edit Skill\n",
                "command_template": {"argv": ["{{file_path}}"]},
                "executable_entry": "script.py",
                "script_body": "#!/usr/bin/env python3\nprint('ok')\n",
            }
        )
        assert result["id"] == "SK-LOCAL-EDIT"

        allowed = skills_runtime.apply_skill("SK-LOCAL-EDIT", dry_run=True)
        assert allowed["ok"] is True
        assert allowed["approval_state"]["execution_level"] == "local"
        assert allowed["approval_state"]["approved_at"]

        check = doctor_runtime.check_skill_health()
        assert check.status == "healthy"

        approved = skills_runtime.approve_skill_execution(
            "SK-LOCAL-EDIT",
            execution_level="local",
            approved_by="Francisco",
        )
        assert approved["approved_by"] == "Francisco"

    def test_packaged_installs_keep_core_and_personal_skills_separate(self, tmp_path, monkeypatch):
        install_home = tmp_path / "installed-nexo"
        for dirname in ["data", "skills", "skills-core", "skills-runtime", "crons"]:
            (install_home / dirname).mkdir(parents=True, exist_ok=True)
        (install_home / "crons" / "manifest.json").write_text(json.dumps({"crons": []}))

        _write_skill_definition(
            install_home / "skills-core",
            "core-doctor",
            {
                "id": "SK-CORE-DOCTOR",
                "name": "Core Doctor",
                "description": "Packaged core skill",
                "level": "published",
                "mode": "guide",
                "trigger_patterns": ["core doctor"],
            },
            guide="# Core Doctor\n",
        )
        _write_skill_definition(
            install_home / "skills",
            "personal-backup",
            {
                "id": "SK-PERSONAL-BACKUP",
                "name": "Personal Backup",
                "description": "Personal skill",
                "level": "published",
                "mode": "guide",
                "trigger_patterns": ["personal backup"],
            },
            guide="# Personal Backup\n",
        )

        monkeypatch.setenv("NEXO_HOME", str(install_home))
        monkeypatch.setenv("NEXO_CODE", str(install_home))

        db, skills_runtime, _, db_skills = _reload_skill_stack()
        db.init_db()
        result = skills_runtime.sync_skills()

        assert sorted(result["ids"]) == ["SK-CORE-DOCTOR", "SK-PERSONAL-BACKUP"]
        assert db_skills.CORE_SKILLS_DIR == install_home / "skills-core"
        assert db_skills.PERSONAL_SKILLS_DIR == install_home / "skills"

        core = db.get_skill("SK-CORE-DOCTOR")
        personal = db.get_skill("SK-PERSONAL-BACKUP")
        assert core["source_kind"] == "core"
        assert personal["source_kind"] == "personal"

    def test_scriptable_candidates_promote_to_executable_drafts(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        created = db.create_skill(
            skill_id="SK-GUIDE-ONLY",
            name="Guide Only Skill",
            description="A repeated guide workflow.",
            level="published",
            content="# Guide Only Skill\n\n## Steps\n1. Do the thing\n",
            steps=["Do the thing"],
            gotchas=["Watch the logs"],
            trigger_patterns=["do the thing"],
        )
        assert created["id"] == "SK-GUIDE-ONLY"

        for context in ("ctx-1", "ctx-2", "ctx-3"):
            db.record_skill_usage("SK-GUIDE-ONLY", success=True, context=context, notes="ok")

        candidates = skills_runtime.list_evolution_candidates()
        assert any(item["id"] == "SK-GUIDE-ONLY" for item in candidates["scriptable"])

        promotion = skills_runtime.auto_promote_skill_evolution()
        assert any(item["id"] == "SK-GUIDE-ONLY" for item in promotion["promoted"])

        evolved = db.get_skill("SK-GUIDE-ONLY")
        assert evolved["mode"] == "hybrid"
        assert evolved["file_path"]
        assert Path(evolved["file_path"]).is_file()
        dry = skills_runtime.apply_skill("SK-GUIDE-ONLY", dry_run=True)
        assert dry["ok"] is True
        assert dry["resolved_mode"] == "hybrid"

    def test_skill_test_retire_promote_and_compose_cover_full_lifecycle(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        db.create_skill(
            skill_id="SK-FIRST",
            name="First Skill",
            description="First component.",
            level="draft",
            content="# First Skill\n",
            steps=["Open workflow"],
            gotchas=["Check logs"],
            trigger_patterns=["first flow"],
            tags=["ops"],
        )
        db.create_skill(
            skill_id="SK-SECOND",
            name="Second Skill",
            description="Second component.",
            level="published",
            content="# Second Skill\n",
            steps=["Close workflow"],
            trigger_patterns=["second flow"],
            tags=["release"],
        )

        tested = skills_runtime.test_skill("SK-FIRST", mode="guide")
        assert tested["ok"] is True
        assert tested["tested"] is True
        assert tested["test_kind"] == "dry_run"

        promoted = skills_runtime.promote_skill("SK-FIRST", target_level="published", reason="Ready for broader use")
        assert promoted["ok"] is True
        assert promoted["level"] == "published"

        composed = skills_runtime.compose_skills(
            new_skill_id="SK-COMPOSED",
            name="Composed Skill",
            component_ids=["SK-FIRST", "SK-SECOND"],
            description="Combined lifecycle",
            mode="guide",
            level="draft",
        )
        assert composed["ok"] is True
        combo = db.get_skill("SK-COMPOSED")
        assert combo is not None
        assert "Open workflow" in combo["content"]
        assert "Close workflow" in combo["content"]

        retired = skills_runtime.retire_skill("SK-SECOND", replacement_id="SK-COMPOSED", reason="Superseded by composed skill")
        assert retired["ok"] is True
        archived = db.get_skill("SK-SECOND")
        assert archived["level"] == "archived"

    def test_outcome_pattern_can_seed_skill_candidate(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        _seed_outcome_pattern(db, selected_choice="staged_validation", count=4)

        candidates = skills_runtime.list_evolution_candidates()
        pattern = next(item for item in candidates["outcome_patterns"] if item["selected_choice"] == "staged_validation")

        seeded = skills_runtime.materialize_outcome_pattern_skill(pattern["pattern_key"])
        seeded_again = skills_runtime.materialize_outcome_pattern_skill(pattern["pattern_key"])
        skill = db.get_skill(seeded["skill"]["id"])
        linked = json.loads(skill["linked_learnings"])

        assert pattern["suggested_skill_candidate"] is True
        assert seeded["ok"] is True
        assert seeded["created"] is True
        assert skill["level"] == "draft"
        assert skill["source_kind"] == "personal"
        assert skill["mode"] == "guide"
        assert linked
        assert seeded_again["ok"] is True
        assert seeded_again["created"] is False
        assert seeded_again["skill"]["id"] == seeded["skill"]["id"]

    def test_skill_outcome_review_can_promote_and_retire_from_evidence(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        _seed_outcome_pattern(db, selected_choice="staged_validation", count=6)
        seeded = skills_runtime.materialize_outcome_pattern_skill(
            next(
                item["pattern_key"]
                for item in skills_runtime.list_evolution_candidates()["outcome_patterns"]
                if item["selected_choice"] == "staged_validation"
            )
        )
        skill_id = seeded["skill"]["id"]

        review = skills_runtime.review_skill_outcomes(skill_id)
        promoted = skills_runtime.review_skill_outcomes(skill_id, auto_apply=True)
        promoted_skill = db.get_skill(skill_id)

        db.create_skill(
            skill_id="SK-RISKY-HOTFIX",
            name="Risky Hotfix",
            description="A bad emergency pattern that should be retired when outcomes stay poor.",
            level="published",
            content="# Risky Hotfix\n",
            trigger_patterns=["risky_hotfix"],
        )
        _seed_outcome_pattern(db, selected_choice="risky_hotfix", count=4, success=False)
        retire_review = skills_runtime.review_skill_outcomes("SK-RISKY-HOTFIX")
        retired = skills_runtime.review_skill_outcomes("SK-RISKY-HOTFIX", auto_apply=True)
        retired_skill = db.get_skill("SK-RISKY-HOTFIX")

        assert review["review"]["recommended_action"] == "promote_published"
        assert promoted["auto_applied"] is True
        assert promoted["applied_action"] == "promote_published"
        assert promoted_skill["level"] == "published"
        assert retire_review["review"]["recommended_action"] == "retire"
        assert retired["auto_applied"] is True
        assert retired["applied_action"] == "retire"
        assert retired_skill["level"] == "archived"

    def test_match_skills_prefers_positive_outcome_evidence(self, skills_env):
        db, _, _, _ = _reload_skill_stack()
        db.init_db()

        db.create_skill(
            skill_id="SK-SAFE-ROLLOUT",
            name="Safe Rollout",
            description="Conservative rollout pattern.",
            level="published",
            content="# Safe Rollout\n",
            trigger_patterns=["safe_rollout"],
            tags=["release", "ops"],
            trust_score=55,
        )
        db.create_skill(
            skill_id="SK-RISKY-HOTFIX",
            name="Risky Hotfix",
            description="Aggressive rollout pattern.",
            level="published",
            content="# Risky Hotfix\n",
            trigger_patterns=["risky_hotfix"],
            tags=["release", "ops"],
            trust_score=90,
        )
        _seed_outcome_pattern(db, selected_choice="safe_rollout", count=6)
        _seed_outcome_pattern(db, selected_choice="risky_hotfix", count=4, success=False)

        matches = db.match_skills("release ops", top_n=2)

        assert [item["id"] for item in matches] == ["SK-SAFE-ROLLOUT", "SK-RISKY-HOTFIX"]
        assert matches[0]["_outcome_review"]["has_evidence"] is True
        assert matches[0]["_outcome_rank"] > 0
        assert matches[1]["_outcome_review"]["recommended_action"] == "retire"


class TestSkillsCli:
    def test_cli_sync_list_get_and_featured(self, skills_env):
        sync_result = _run_cli(skills_env, "skills", "sync", "--json")
        assert sync_result.returncode == 0
        sync_data = json.loads(sync_result.stdout)
        assert "SK-RUN-RUNTIME-DOCTOR" in sync_data["ids"]

        list_result = _run_cli(skills_env, "skills", "list", "--json")
        assert list_result.returncode == 0
        list_data = json.loads(list_result.stdout)
        assert any(skill["id"] == "SK-RUN-RUNTIME-DOCTOR" for skill in list_data)

        get_result = _run_cli(skills_env, "skills", "get", "SK-RUN-RUNTIME-DOCTOR", "--json")
        assert get_result.returncode == 0
        skill = json.loads(get_result.stdout)
        assert skill["source_kind"] == "core"
        assert skill["mode"] == "execute"

        featured_result = _run_cli(skills_env, "skills", "featured", "--json")
        assert featured_result.returncode == 0
        featured = json.loads(featured_result.stdout)
        assert any(skill["id"] == "SK-RUN-RUNTIME-DOCTOR" for skill in featured)

    def test_cli_apply_runs_without_manual_approval(self, skills_env):
        skill_dir = skills_env / "skills" / "local-release"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.json").write_text(
            json.dumps(
                {
                    "id": "SK-LOCAL-RELEASE",
                    "name": "Local Release",
                    "description": "Release helper that edits local files.",
                    "level": "published",
                    "mode": "execute",
                    "execution_level": "local",
                    "approval_required": True,
                    "command_template": {"argv": ["{{file_path}}"]},
                    "executable_entry": "script.py",
                },
                indent=2,
            )
            + "\n"
        )
        (skill_dir / "guide.md").write_text("# Local Release\n")
        script = skill_dir / "script.py"
        script.write_text("#!/usr/bin/env python3\nprint('release')\n")
        script.chmod(0o755)

        sync_result = _run_cli(skills_env, "skills", "sync", "--json")
        assert sync_result.returncode == 0

        dry = _run_cli(skills_env, "skills", "apply", "SK-LOCAL-RELEASE", "--dry-run", "--json")
        assert dry.returncode == 0
        dry_data = json.loads(dry.stdout)
        assert dry_data["ok"] is True
        assert dry_data["approval_state"]["approved_at"]

        approved = _run_cli(
            skills_env,
            "skills",
            "approve",
            "SK-LOCAL-RELEASE",
            "--execution-level",
            "local",
            "--approved-by",
            "Francisco",
            "--json",
        )
        assert approved.returncode == 0
        approved_data = json.loads(approved.stdout)
        assert approved_data["approved_by"] == "Francisco"
        assert approved_data["approved_at"]

    def test_cli_skill_lifecycle_commands(self, skills_env):
        db, _, _, _ = _reload_skill_stack()
        db.init_db()
        db.create_skill(
            skill_id="SK-CLI-A",
            name="CLI A",
            description="First CLI skill",
            level="draft",
            content="# CLI A\n",
            steps=["Do A"],
            trigger_patterns=["cli a"],
        )
        db.create_skill(
            skill_id="SK-CLI-B",
            name="CLI B",
            description="Second CLI skill",
            level="published",
            content="# CLI B\n",
            steps=["Do B"],
            trigger_patterns=["cli b"],
        )

        tested = _run_cli(skills_env, "skills", "test", "SK-CLI-A", "--json")
        assert tested.returncode == 0
        tested_payload = json.loads(tested.stdout)
        assert tested_payload["tested"] is True

        promoted = _run_cli(
            skills_env,
            "skills",
            "promote",
            "SK-CLI-A",
            "--target-level",
            "published",
            "--json",
        )
        assert promoted.returncode == 0
        promoted_payload = json.loads(promoted.stdout)
        assert promoted_payload["level"] == "published"

        composed = _run_cli(
            skills_env,
            "skills",
            "compose",
            "SK-CLI-C",
            "CLI Composed",
            "--component-ids",
            '["SK-CLI-A","SK-CLI-B"]',
            "--json",
        )
        assert composed.returncode == 0
        composed_payload = json.loads(composed.stdout)
        assert composed_payload["skill_id"] == "SK-CLI-C"

        retired = _run_cli(
            skills_env,
            "skills",
            "retire",
            "SK-CLI-B",
            "--replacement-id",
            "SK-CLI-C",
            "--json",
        )
        assert retired.returncode == 0
        retired_payload = json.loads(retired.stdout)
        assert retired_payload["level"] == "archived"

    def test_cli_skill_outcome_review(self, skills_env):
        db, skills_runtime, _, _ = _reload_skill_stack()
        db.init_db()

        _seed_outcome_pattern(db, selected_choice="staged_validation", count=4)
        pattern_key = next(
            item["pattern_key"]
            for item in skills_runtime.list_evolution_candidates()["outcome_patterns"]
            if item["selected_choice"] == "staged_validation"
        )
        seeded = skills_runtime.materialize_outcome_pattern_skill(pattern_key)

        review = _run_cli(skills_env, "skills", "outcome-review", seeded["skill"]["id"], "--json")
        assert review.returncode == 0
        review_payload = json.loads(review.stdout)
        assert review_payload["review"]["recommended_action"] == "promote_published"

        auto = _run_cli(skills_env, "skills", "outcome-review", seeded["skill"]["id"], "--auto-apply", "--json")
        assert auto.returncode == 0
        auto_payload = json.loads(auto.stdout)
        assert auto_payload["auto_applied"] is True
        assert auto_payload["level"] == "published"
