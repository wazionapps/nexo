from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_release_readiness_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_closeout_db(
    db_path: Path,
    *,
    task_id: str = "PT-1",
    status: str = "done",
    must_verify: int = 1,
    close_evidence: str = "pytest -q tests/test_protocol.py",
    must_change_log: int = 1,
    change_log_id: int | None = 7,
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT,
            must_verify INTEGER,
            close_evidence TEXT,
            must_change_log INTEGER,
            change_log_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE change_log (
            id INTEGER PRIMARY KEY,
            what_changed TEXT,
            why TEXT,
            verify TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO protocol_tasks VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, status, must_verify, close_evidence, must_change_log, change_log_id),
    )
    if change_log_id:
        conn.execute(
            "INSERT INTO change_log VALUES (?, ?, ?, ?)",
            (change_log_id, "Updated release closeout gates", "Harden final release audit", close_evidence),
        )
    conn.commit()
    conn.close()


def _seed_public_surfaces(root: Path, version: str, *, include_well_known: bool = False):
    version_slug = version.replace(".", "-")
    version_anchor = version.replace(".", "")
    (root / "README.md").write_text(
        f"Version `{version}` is the current packaged-runtime line\n",
        encoding="utf-8",
    )
    (root / "llms.txt").write_text(
        f"> Open-source cognitive runtime with a shared brain (v{version}).\n\n"
        f"v{version}: latest release summary\n",
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        f'<span id="version-badge">v{version}</span>\n'
        f'"softwareVersion": "{version}"\n'
        f'<a href="/changelog/#v{version_anchor}">Latest release</a>\n',
        encoding="utf-8",
    )
    for route in (
        "features/index.html",
        "evolution/index.html",
        "compare/index.html",
        "docs/index.html",
        "demos/index.html",
        "solutions/index.html",
        "watch/index.html",
    ):
        path = root / route
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<html><body>{route}</body></html>\n", encoding="utf-8")
    blog_dir = root / "blog"
    blog_dir.mkdir(exist_ok=True)
    (blog_dir / "index.html").write_text(
        f'<a href="/blog/nexo-{version_slug}/">Read latest release</a>\n'
        f"<h2>NEXO {version}: Release</h2>\n"
        f'<a href="/changelog/#v{version_anchor}">Open changelog</a>\n',
        encoding="utf-8",
    )
    changelog_dir = root / "changelog"
    changelog_dir.mkdir(exist_ok=True)
    (changelog_dir / "index.html").write_text(
        f'<section id="v{version_anchor}">New in v{version}</section>\n'
        f"<a>Start with v{version}</a>\n",
        encoding="utf-8",
    )
    (root / "sitemap.xml").write_text(
        f"<loc>https://nexo-brain.com/features/</loc>\n"
        f"https://nexo-brain.com/blog/nexo-{version_slug}/\n",
        encoding="utf-8",
    )
    if include_well_known:
        well_known = root / ".well-known"
        well_known.mkdir(exist_ok=True)
        (well_known / "llms.txt").write_text(
            f"> Open-source cognitive runtime with a shared brain (v{version}).\n\n"
            f"v{version}: latest release summary\n",
            encoding="utf-8",
        )


def _seed_smoke_artifact(root: Path, version: str, *, groups: list[dict] | None = None, ok: bool = True):
    smoke_dir = root / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "generated_at": "2026-04-22T05:30:00+00:00",
        "ok": ok,
        "groups": groups or [
            {
                "id": "core",
                "ok": True,
                "returncode": 0,
                "command": ["python3", "-m", "pytest", "-q", "tests/test_protocol.py"],
                "targets": ["tests/test_protocol.py"],
                "duration_seconds": 1.2,
            }
        ],
    }
    (smoke_dir / f"v{version}.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_runtime_memory_benchmark_summary(
    root: Path,
    *,
    status: str = "pass",
    mode: str = "block",
    missing: list[str] | None = None,
    failures: list[str] | None = None,
):
    missing = missing or []
    failures = failures or []
    spec_refs = [f"{index:02d}" for index in range(1, 11)]
    covered = [ref for ref in spec_refs if ref not in missing]
    summary_dir = root / "benchmarks" / "runtime_pack" / "results"
    summary_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": "release",
        "case_summary": {
            "case_count": len(covered),
            "required_spec_refs": spec_refs,
            "covered_spec_refs": covered,
            "missing_spec_refs": missing,
            "failures": failures,
            "ok": not missing and not failures,
        },
        "release_gate": {
            "id": "runtime_memory_benchmark",
            "mode": mode,
            "status": status,
            "required_spec_refs": spec_refs,
            "covered_spec_refs": covered,
            "missing_spec_refs": missing,
            "failure_count": len(failures),
            "failures": failures,
        },
    }
    (summary_dir / "latest_summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_check_contract_accepts_valid_contract(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")

    contract = {
        "release_line": "v4.5",
        "target_version": "4.5.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "gates": [
            {
                "id": "manual",
                "title": "Manual",
                "status": "complete",
                "evidence_required": ["doc exists"],
            }
        ],
    }

    module._check_contract(
        contract,
        contract_path=tmp_path / "contract.json",
        website_root=website_root,
        nexo_home=tmp_path / "nexo-home",
        require_complete=True,
        repo_root=tmp_path,
    )


def test_check_contract_runs_runtime_memory_benchmark_gate(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")
    _seed_runtime_memory_benchmark_summary(tmp_path)

    contract = {
        "release_line": "v7.28",
        "target_version": "7.28.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "gates": [
            {
                "id": "runtime_memory_benchmark",
                "title": "Runtime memory benchmark",
                "status": "complete",
                "evidence_required": ["benchmarks/runtime_pack/results/latest_summary.json"],
            }
        ],
    }

    module._check_contract(
        contract,
        contract_path=tmp_path / "contract.json",
        website_root=website_root,
        nexo_home=tmp_path / "nexo-home",
        require_complete=True,
        repo_root=tmp_path,
    )


def test_check_contract_blocks_runtime_memory_benchmark_failures(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")
    _seed_runtime_memory_benchmark_summary(
        tmp_path,
        status="block",
        missing=["02"],
        failures=["missing spec coverage: 02"],
    )

    contract = {
        "release_line": "v7.28",
        "target_version": "7.28.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "gates": [
            {
                "id": "runtime_memory_benchmark",
                "title": "Runtime memory benchmark",
                "status": "complete",
                "evidence_required": ["benchmarks/runtime_pack/results/latest_summary.json"],
            }
        ],
    }

    with pytest.raises(SystemExit, match="runtime memory benchmark did not pass"):
        module._check_contract(
            contract,
            contract_path=tmp_path / "contract.json",
            website_root=website_root,
            nexo_home=tmp_path / "nexo-home",
            require_complete=True,
            repo_root=tmp_path,
        )


def test_check_contract_rejects_wrong_distribution(tmp_path):
    module = _load_module()
    contract = {
        "release_line": "v4.5",
        "target_version": "4.5.0",
        "distribution": {
            "git_updates_on": "tag_publish",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": [],
        "required_website_files": ["index.html"],
        "gates": [
            {
                "id": "manual",
                "title": "Manual",
                "status": "complete",
                "evidence_required": ["doc exists"],
            }
        ],
    }

    with pytest.raises(SystemExit, match="git_updates_on=merge_to_main"):
        module._check_contract(
            contract,
            contract_path=tmp_path / "contract.json",
            website_root=tmp_path / "site",
            nexo_home=tmp_path / "nexo-home",
            require_complete=False,
            repo_root=tmp_path,
        )


def test_check_contract_requires_completion_when_requested(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")

    contract = {
        "release_line": "v4.5",
        "target_version": "4.5.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "gates": [
            {
                "id": "manual",
                "title": "Manual",
                "status": "in_progress",
                "evidence_required": ["doc exists"],
            }
        ],
    }

    with pytest.raises(SystemExit, match="incomplete gates"):
        module._check_contract(
            contract,
            contract_path=tmp_path / "contract.json",
            website_root=website_root,
            nexo_home=tmp_path / "nexo-home",
            require_complete=True,
            repo_root=tmp_path,
        )


def test_check_contract_validates_critical_surfaces_and_publication_state(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    nexo_home = tmp_path / "nexo-home"
    (nexo_home / "managed").mkdir(parents=True)
    (nexo_home / "managed" / "surface.txt").write_text("release-ready marker\n", encoding="utf-8")
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")

    contract = {
        "release_line": "v4.5",
        "target_version": "4.5.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "critical_surfaces": [
            {
                "id": "managed_surface",
                "path": "{nexo_home}/managed/surface.txt",
                "kind": "file",
                "markers": ["release-ready marker"],
            }
        ],
        "publication": {
            "status": "ready",
            "checklist_complete": True,
            "blockers": [
                {"title": "resolved blocker", "severity": "high", "status": "resolved"}
            ],
        },
        "gates": [
            {
                "id": "manual",
                "title": "Manual",
                "status": "complete",
                "evidence_required": ["doc exists"],
            }
        ],
    }

    module._check_contract(
        contract,
        contract_path=tmp_path / "contract.json",
        website_root=website_root,
        nexo_home=nexo_home,
        require_complete=True,
        repo_root=tmp_path,
    )


def test_check_contract_rejects_open_high_publication_blocker(tmp_path):
    module = _load_module()
    website_root = tmp_path / "site"
    website_root.mkdir()
    nexo_home = tmp_path / "nexo-home"
    (tmp_path / "repo-artifact.txt").write_text("ok", encoding="utf-8")
    (website_root / "index.html").write_text("ok", encoding="utf-8")

    contract = {
        "release_line": "v4.5",
        "target_version": "4.5.0",
        "distribution": {
            "git_updates_on": "merge_to_main",
            "packaged_release_on": "tag_publish",
        },
        "required_repo_files": ["repo-artifact.txt"],
        "required_website_files": ["index.html"],
        "publication": {
            "status": "blocked",
            "checklist_complete": True,
            "blockers": [
                {"title": "desktop smoke broken", "severity": "high", "status": "open"}
            ],
        },
        "gates": [
            {
                "id": "manual",
                "title": "Manual",
                "status": "complete",
                "evidence_required": ["doc exists"],
            }
        ],
    }

    with pytest.raises(SystemExit, match="publication blocked by open high-severity blockers"):
        module._check_contract(
            contract,
            contract_path=tmp_path / "contract.json",
            website_root=website_root,
            nexo_home=nexo_home,
            require_complete=True,
            repo_root=tmp_path,
        )


def test_check_duplicate_artifacts_accepts_clean_tree(tmp_path):
    module = _load_module()
    (tmp_path / "alpha.py").write_text("print('ok')\n", encoding="utf-8")

    module._check_duplicate_artifacts(tmp_path)


def test_check_duplicate_artifacts_rejects_duplicate_copy(tmp_path):
    module = _load_module()
    (tmp_path / "alpha.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "alpha 2.py").write_text("print('old')\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="duplicate artifacts found"):
        module._check_duplicate_artifacts(tmp_path)


def test_check_duplicate_artifacts_ignores_generated_dirs(tmp_path):
    module = _load_module()
    ignored = tmp_path / ".git" / "logs"
    ignored.mkdir(parents=True)
    (ignored / "alpha.py").write_text("print('ok')\n", encoding="utf-8")
    (ignored / "alpha 2.py").write_text("print('old')\n", encoding="utf-8")
    node_modules = tmp_path / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "beta.py").write_text("print('ok')\n", encoding="utf-8")
    (node_modules / "beta 2.py").write_text("print('old')\n", encoding="utf-8")

    module._check_duplicate_artifacts(tmp_path)


def test_check_repo_public_surfaces_accepts_aligned_files(tmp_path):
    module = _load_module()
    _seed_public_surfaces(tmp_path, "5.3.29")

    module._check_repo_public_surfaces("5.3.29", repo_root=tmp_path)


def test_check_repo_public_surfaces_rejects_drift(tmp_path):
    module = _load_module()
    _seed_public_surfaces(tmp_path, "5.3.29")
    (tmp_path / "README.md").write_text("Version `5.3.28` is the current packaged-runtime line\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="repo public surfaces drift"):
        module._check_repo_public_surfaces("5.3.29", repo_root=tmp_path)


def test_check_repo_public_surfaces_rejects_missing_required_route(tmp_path):
    module = _load_module()
    _seed_public_surfaces(tmp_path, "5.3.29")
    (tmp_path / "features" / "index.html").unlink()

    with pytest.raises(SystemExit, match="features/index.html missing file"):
        module._check_repo_public_surfaces("5.3.29", repo_root=tmp_path)


def test_check_website_rejects_missing_current_release_markers(tmp_path):
    module = _load_module()
    _seed_public_surfaces(tmp_path, "5.3.29", include_well_known=True)
    (tmp_path / "blog" / "index.html").write_text(
        '<a href="/blog/nexo-5-3-24-model-defaults-and-headless-safe-update/">Read latest release</a>\n'
        '<a href="/changelog/#v5329">Open changelog</a>\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="website drift"):
        module._check_website("5.3.29", tmp_path)


def test_check_protocol_closeout_accepts_done_task_with_change_log(tmp_path):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    db_dir = nexo_home / "data"
    db_dir.mkdir(parents=True)
    _seed_closeout_db(db_dir / "nexo.db", task_id="PT-200")

    module._check_protocol_closeout(nexo_home, "PT-200")


def test_check_protocol_closeout_uses_runtime_data_db(tmp_path):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    db_dir = nexo_home / "runtime" / "data"
    db_dir.mkdir(parents=True)
    _seed_closeout_db(db_dir / "nexo.db", task_id="PT-202")

    module._check_protocol_closeout(nexo_home, "PT-202")


def test_check_protocol_closeout_rejects_missing_change_log(tmp_path):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    db_dir = nexo_home / "data"
    db_dir.mkdir(parents=True)
    _seed_closeout_db(db_dir / "nexo.db", task_id="PT-201", change_log_id=None)

    with pytest.raises(SystemExit, match="missing change_log_id"):
        module._check_protocol_closeout(nexo_home, "PT-201")


def test_check_smoke_artifact_accepts_passing_artifact(tmp_path):
    module = _load_module()
    _seed_smoke_artifact(tmp_path, "5.3.29")

    module._check_smoke_artifact("5.3.29", smoke_root=tmp_path / "smoke")


def test_check_smoke_artifact_rejects_missing_required_group(tmp_path):
    module = _load_module()
    _seed_smoke_artifact(tmp_path, "5.3.29")

    with pytest.raises(SystemExit, match="required smoke groups missing"):
        module._check_smoke_artifact(
            "5.3.29",
            contract={"smoke": {"required_groups": ["desktop_install"]}},
            smoke_root=tmp_path / "smoke",
        )


def test_check_smoke_artifact_rejects_missing_artifact(tmp_path):
    module = _load_module()

    with pytest.raises(SystemExit, match="smoke artifact missing"):
        module._check_smoke_artifact("5.3.29", smoke_root=tmp_path / "smoke")


def test_runtime_doctor_closeout_blocks_only_installed_product_criticals(tmp_path, monkeypatch):
    module = _load_module()
    captured = {}

    def fake_run(cmd, *, env=None):
        captured["cmd"] = cmd
        captured["env"] = env

    monkeypatch.setattr(module, "_run", fake_run)

    module._run_runtime_doctor(tmp_path / "nexo-home")

    inline = captured["cmd"][2]
    assert "category" in inline
    assert "installed_product" in inline
    assert "blocking" in inline
    assert "operator_history" not in inline


def test_main_final_closeout_requires_protocol_task_id(tmp_path, monkeypatch):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    nexo_home.mkdir()

    monkeypatch.setattr(module, "_package_manifest", lambda: {"name": "nexo-brain", "version": "5.3.11", "repository": {"url": "git+https://github.com/wazionapps/nexo.git"}})
    monkeypatch.setattr(module, "_check_changelog", lambda version: None)
    monkeypatch.setattr(module, "_check_website", lambda version, website_root: None)
    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_resolve_nexo_home", lambda explicit_home="": nexo_home)
    monkeypatch.setattr(sys, "argv", ["verify_release_readiness.py", "--final-closeout"])

    with pytest.raises(SystemExit, match="requires --protocol-task-id"):
        module.main()


def test_main_require_smoke_checks_smoke_artifact(tmp_path, monkeypatch):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    nexo_home.mkdir()

    smoke_calls = []
    monkeypatch.setattr(module, "_package_manifest", lambda: {"name": "nexo-brain", "version": "5.3.11", "repository": {"url": "git+https://github.com/wazionapps/nexo.git"}})
    monkeypatch.setattr(module, "_check_changelog", lambda version: None)
    monkeypatch.setattr(module, "_check_repo_public_surfaces", lambda version, repo_root=module.ROOT: None)
    monkeypatch.setattr(module, "_check_duplicate_artifacts", lambda repo_root=module.ROOT: None)
    monkeypatch.setattr(module, "_check_website", lambda version, website_root: None)
    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_resolve_nexo_home", lambda explicit_home="": nexo_home)
    monkeypatch.setattr(module, "_run_runtime_doctor", lambda home: None)
    monkeypatch.setattr(module, "_check_smoke_artifact", lambda version, contract=None, smoke_root=module.DEFAULT_SMOKE_ROOT: smoke_calls.append((version, contract)))
    monkeypatch.setattr(sys, "argv", ["verify_release_readiness.py", "--require-smoke"])

    module.main()

    assert smoke_calls == [("5.3.11", None)]
