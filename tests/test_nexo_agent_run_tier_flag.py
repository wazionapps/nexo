"""v6.0.2 — nexo-agent-run.py --tier flag propagates to run_automation_prompt.

Loads the CLI module, monkeypatches ``run_automation_prompt`` to a
recorder, and invokes ``main`` with the new flags. The recorder captures
the kwargs the CLI passed through.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPT = SRC / "scripts" / "nexo-agent-run.py"

for path in (str(SRC), str(SCRIPT.parent)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_cli_module():
    """Load ``nexo-agent-run.py`` as a module via importlib (the script
    name contains a dash, so ``import`` statements don't work)."""
    spec = importlib.util.spec_from_file_location("nexo_agent_run_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_tier_and_caller_flags_reach_run_automation_prompt(monkeypatch):
    module = _load_cli_module()
    captured: dict = {}

    def _fake_run(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        result = types.SimpleNamespace(stdout="", stderr="", returncode=0)
        return result

    monkeypatch.setattr(module, "run_automation_prompt", _fake_run)

    rc = module.main(
        [
            "--prompt",
            "noop",
            "--caller",
            "personal/foo",
            "--tier",
            "maximo",
        ]
    )
    assert rc == 0
    assert captured["caller"] == "personal/foo"
    assert captured["tier"] == "maximo"


def test_tier_flag_defaults_to_empty(monkeypatch):
    module = _load_cli_module()
    captured: dict = {}

    def _fake_run(prompt, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_automation_prompt", _fake_run)

    module.main(["--prompt", "noop"])
    assert captured.get("tier") == ""
    assert captured.get("caller") == "agent_run/generic"
