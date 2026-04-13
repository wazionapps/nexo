from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_synthesis_module(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("scripts.nexo-synthesis", None)
    import importlib.util

    spec = importlib.util.spec_from_file_location("scripts.nexo-synthesis", REPO_SRC / "scripts" / "nexo-synthesis.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_data_reads_actionable_coordination_summaries(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    coord = home / "coordination"
    coord.mkdir(parents=True)

    (coord / "followup-hygiene-summary.json").write_text(json.dumps({
        "date": "2026-04-13",
        "dirty_normalized": 0,
        "stale_count": 2,
        "orphan_count": 1,
        "stale_ids": ["NF-1", "NF-2"],
        "orphan_ids": ["NF-3"],
    }))
    (coord / "outcome-checker-summary.json").write_text(json.dumps({
        "checked_at": "2026-04-13T08:00:00",
        "checked": 3,
        "met": 1,
        "missed": 1,
        "pending": 1,
        "errors": 0,
        "ids": [1, 2, 3],
    }))

    synthesis = _load_synthesis_module(monkeypatch, home)
    data = synthesis.collect_data()

    assert data["followup_hygiene_summary"]["stale_count"] == 2
    assert data["outcome_checker_summary"]["missed"] == 1


def test_collect_data_omits_non_actionable_coordination_summaries(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    coord = home / "coordination"
    coord.mkdir(parents=True)

    (coord / "followup-hygiene-summary.json").write_text(json.dumps({
        "date": "2026-04-13",
        "dirty_normalized": 0,
        "stale_count": 0,
        "orphan_count": 0,
        "stale_ids": [],
        "orphan_ids": [],
    }))
    (coord / "outcome-checker-summary.json").write_text(json.dumps({
        "checked_at": "2026-04-13T08:00:00",
        "checked": 0,
        "met": 0,
        "missed": 0,
        "pending": 0,
        "errors": 0,
        "ids": [],
    }))

    synthesis = _load_synthesis_module(monkeypatch, home)
    data = synthesis.collect_data()

    assert "followup_hygiene_summary" not in data
    assert "outcome_checker_summary" not in data


def test_collect_data_reads_actionable_update_summary(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    logs = home / "logs"
    logs.mkdir(parents=True)

    (logs / "update-last-summary.json").write_text(json.dumps({
        "timestamp": "2026-04-13T09:00:00",
        "updated": False,
        "deferred_reason": "source repo has local changes",
        "actions": ["power:enabled"],
        "client_bootstrap_updates": ["CLAUDE.md v2.1.4: already current"],
    }))

    synthesis = _load_synthesis_module(monkeypatch, home)
    data = synthesis.collect_data()

    assert data["update_summary"]["deferred_reason"] == "source repo has local changes"


def test_collect_data_omits_non_actionable_update_summary(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    logs = home / "logs"
    logs.mkdir(parents=True)

    (logs / "update-last-summary.json").write_text(json.dumps({
        "timestamp": "2026-04-13T09:00:00",
        "updated": False,
        "deferred_reason": None,
        "git_update": None,
        "npm_notice": None,
        "error": None,
        "actions": ["power:enabled"],
        "client_bootstrap_updates": ["CLAUDE.md v2.1.4: already current"],
    }))

    synthesis = _load_synthesis_module(monkeypatch, home)
    data = synthesis.collect_data()

    assert "update_summary" not in data
