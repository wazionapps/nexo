"""Python driver for cross-engine parity fixtures.

Feeds tests/parity/fixtures.json sequences into HeadlessEnforcer and
asserts the expected rule_ids end up in the injection queue (order
independent). The JS driver at tests/parity/js-driver.js (in the
desktop repo) runs the same fixtures against EnforcementEngine; the
two drivers together guarantee no behavioural drift.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))


FIXTURES = Path(__file__).parent / "fixtures.json"


@pytest.fixture(autouse=True)
def _isolated(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["enforcement_engine", "guardian_config"]:
        importlib.reload(importlib.import_module(mod))
    yield


def _load_cases():
    data = json.loads(FIXTURES.read_text())
    return data.get("cases") or []


def _apply_seed(seed):
    from db import create_entity
    if seed["op"] == "seed_host":
        create_entity(
            name=seed["name"],
            type="host",
            value=json.dumps(seed.get("metadata") or {"aliases": []}),
        )


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_parity_python(case, tmp_path):
    from enforcement_engine import HeadlessEnforcer
    import guardian_config
    # Force all rules mentioned in the expectation to hard for deterministic assertions.
    home = tmp_path / f"parity_{case['id']}"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    for rid in case.get("expect_rule_ids") or []:
        default.setdefault("rules", {})[rid] = "hard"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    importlib.reload(guardian_config)
    import enforcement_engine as eng
    importlib.reload(eng)

    enforcer = eng.HeadlessEnforcer()
    for step in case["seq"]:
        op = step["op"]
        if op == "user":
            enforcer.on_user_message(step.get("text", ""))
        elif op == "tool":
            enforcer.on_tool_call(step["name"], step.get("input"))
        elif op.startswith("seed_"):
            _apply_seed(step)
        else:
            raise ValueError(f"unknown op {op}")
    observed = {q.get("rule_id") for q in enforcer.injection_queue if q.get("rule_id")}
    expected = set(case.get("expect_rule_ids") or [])
    strict = bool(case.get("strict", False)) or expected == set()
    if strict:
        # Exact equality: extra injections count as a parity bug (the
        # opposite-engine did not fire them, so the rule sets have drifted).
        assert observed == expected, (
            f"{case['id']}: strict mismatch — expected {expected}, observed {observed}"
        )
    else:
        # Relaxed inclusion for legacy cases: every expected rule MUST
        # fire; extras are tolerated until the case is tightened.
        missing = expected - observed
        assert not missing, f"{case['id']}: expected {expected}, observed {observed}"
