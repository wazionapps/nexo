from __future__ import annotations

import importlib.util
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
