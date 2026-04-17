"""v6.0.0 — UserPromptSubmit/PostToolUse auto_capture must register a
learning on the first correction match and de-duplicate the second
identical match within the 1h TTL window.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
HOOKS = SRC / "hooks"
for p in (str(SRC), str(HOOKS)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _isolated_nexo_home(tmp_path, monkeypatch):
    """Point NEXO_HOME at a tmp dir so the dedup table is per-test."""
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    yield


@pytest.fixture
def auto_capture_mod(monkeypatch):
    """Import auto_capture fresh each test so module-level cognitive
    hooks don't leak state across cases."""
    import importlib
    # Clean prior imports so monkeypatches land correctly.
    for key in ("auto_capture", "cognitive", "tools_learnings"):
        sys.modules.pop(key, None)
    import auto_capture  # type: ignore
    return importlib.reload(auto_capture)


def _stub_cognitive(monkeypatch, auto_capture_mod, *, return_id=42):
    calls = []
    def _fake_ingest(**kwargs):
        calls.append(kwargs)
        return return_id
    monkeypatch.setattr(auto_capture_mod.cognitive, "ingest", _fake_ingest)
    return calls


def _stub_learnings(monkeypatch, *, ok=True):
    import types
    learning_calls = []
    def _add_learning(**kwargs):
        learning_calls.append(kwargs)
        return {"ok": ok, "id": 999 if ok else 0}
    fake_module = types.ModuleType("tools_learnings")
    fake_module.add_learning = _add_learning
    sys.modules["tools_learnings"] = fake_module
    return learning_calls


def test_correction_triggers_learning_add_once(monkeypatch, auto_capture_mod):
    _stub_cognitive(monkeypatch, auto_capture_mod)
    learning_calls = _stub_learnings(monkeypatch)

    result = auto_capture_mod.process_conversation([
        "actually, that's wrong — the user_id lives in the JWT claims, not the header",
    ])

    assert result["corrections"] == 1
    assert result["learnings_added"] == 1
    assert len(learning_calls) == 1
    call = learning_calls[0]
    assert call["category"] == "auto"
    assert call["priority"] == "medium"
    assert "JWT" in call["content"]


def test_same_correction_within_1h_is_deduplicated(monkeypatch, auto_capture_mod):
    _stub_cognitive(monkeypatch, auto_capture_mod)
    learning_calls = _stub_learnings(monkeypatch)

    # Wording picked deliberately to match only the correction patterns;
    # a line that also triggered decision classification would spawn two
    # dedup rows and the persistent-dedup counter below would read 2.
    correction_line = "stop, that's wrong — the cache must stay read-only in production"

    first = auto_capture_mod.process_conversation([correction_line])
    second = auto_capture_mod.process_conversation([correction_line])

    assert first["corrections"] == 1
    assert first["learnings_added"] == 1
    assert second["corrections"] == 1  # still classified as correction…
    assert second["learnings_added"] == 0  # …but dedup prevented a second learning
    assert second["deduplicated_persistent"] == 1
    # And only one learning_add call in total
    assert len(learning_calls) == 1


def test_non_correction_lines_do_not_add_learnings(monkeypatch, auto_capture_mod):
    _stub_cognitive(monkeypatch, auto_capture_mod)
    learning_calls = _stub_learnings(monkeypatch)

    result = auto_capture_mod.process_conversation([
        "decided to go with the faster queue implementation for the v2 pipeline",
        "remember: the migration script runs under cron at 04:30 UTC",
    ])

    assert result["decisions"] == 1
    assert result["explicits"] == 1
    assert result["corrections"] == 0
    assert result["learnings_added"] == 0
    assert learning_calls == []


def test_hook_never_raises_when_learnings_module_missing(monkeypatch, auto_capture_mod):
    _stub_cognitive(monkeypatch, auto_capture_mod)
    # Simulate tools_learnings being unavailable (fresh install, tests, etc).
    sys.modules.pop("tools_learnings", None)
    monkeypatch.setattr(
        auto_capture_mod,
        "_auto_learning_add",
        lambda *a, **kw: False,
    )
    result = auto_capture_mod.process_conversation([
        "no, that's not right — rollback the migration immediately",
    ])
    assert result["corrections"] == 1
    assert result["learnings_added"] == 0
