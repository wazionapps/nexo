from __future__ import annotations

import sys
import time
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _tags(enforcer):
    return [item.get("tag") for item in enforcer.injection_queue]


def _fresh_enforcer(monkeypatch):
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    for name in ("core_prompts", "r37_reality_preflight", "enforcement_engine"):
        sys.modules.pop(name, None)
    from enforcement_engine import HeadlessEnforcer

    return HeadlessEnforcer


def test_reality_preflight_warns_before_sensitive_answer_without_lookup(monkeypatch):
    HeadlessEnforcer = _fresh_enforcer(monkeypatch)

    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R37_reality_preflight"] = "hard"
    enforcer.on_user_message("Que release de NEXO esta publicada ahora?")
    enforcer.on_assistant_text("La release publicada es la ultima.")

    assert "r37:reality-preflight" in _tags(enforcer)


def test_reality_preflight_accepts_recent_atlas_read(monkeypatch):
    HeadlessEnforcer = _fresh_enforcer(monkeypatch)
    from r13_pre_edit_guard import ToolCallRecord

    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R37_reality_preflight"] = "hard"
    enforcer.on_user_message("Que release de NEXO esta publicada ahora?")
    enforcer.recent_tool_records.append(
        ToolCallRecord(
            tool="Read",
            ts=time.time(),
            files=("/Users/franciscoc/.nexo/brain/project-atlas.json",),
        )
    )
    enforcer.on_assistant_text("Segun el atlas y el repo, la release publicada es...")

    assert "r37:reality-preflight" not in _tags(enforcer)
