"""Fase E.5 — CLI quarantine subcommand (Desktop Guardian Proposals bridge).

Validates that `nexo quarantine list|promote|reject` return machine-
parseable JSON that the Desktop renderer can consume.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "src" / "cli.py"


@pytest.fixture
def nexo_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo_home"
    home.mkdir()
    monkeypatch.setenv("NEXO_HOME", str(home))
    return home


def _run(args, cwd=None):
    res = subprocess.run(
        [sys.executable, str(CLI)] + args,
        cwd=cwd or (REPO / "src"),
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    return res


def test_quarantine_list_empty_returns_json(nexo_home):
    res = _run(["quarantine", "list", "--json"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert "items" in payload
    assert isinstance(payload["items"], list)


def test_quarantine_promote_nonexistent_returns_error_json(nexo_home):
    res = _run(["quarantine", "promote", "999999", "--json"])
    # CLI returns 0 but payload has ok:false so caller can differentiate.
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert "not found" in payload["message"]
    assert payload["id"] == 999999


def test_quarantine_reject_nonexistent_returns_error_json(nexo_home):
    res = _run(["quarantine", "reject", "999999", "--reason", "cli test", "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert "not found" in payload["message"]


def test_quarantine_without_subcommand_exits_nonzero(nexo_home):
    res = _run(["quarantine"])
    assert res.returncode != 0


def test_quarantine_list_respects_limit(nexo_home):
    res = _run(["quarantine", "list", "--limit", "5", "--json"])
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert len(payload["items"]) <= 5
