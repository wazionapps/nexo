from __future__ import annotations

import importlib.util
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


def test_check_protocol_closeout_accepts_done_task_with_change_log(tmp_path):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    db_dir = nexo_home / "data"
    db_dir.mkdir(parents=True)
    _seed_closeout_db(db_dir / "nexo.db", task_id="PT-200")

    module._check_protocol_closeout(nexo_home, "PT-200")


def test_check_protocol_closeout_rejects_missing_change_log(tmp_path):
    module = _load_module()
    nexo_home = tmp_path / "nexo-home"
    db_dir = nexo_home / "data"
    db_dir.mkdir(parents=True)
    _seed_closeout_db(db_dir / "nexo.db", task_id="PT-201", change_log_id=None)

    with pytest.raises(SystemExit, match="missing change_log_id"):
        module._check_protocol_closeout(nexo_home, "PT-201")


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
