"""Phase 1.5 — protocol nudge shaping (SPEC-FIABILIDAD-FASES-2026-06 §1.5).

The no-task warning fired on every non-trivial tool from call #1 (noise that
gets ignored). Shaping adds: streak threshold, cooldown, headless skip — in
SHADOW mode by default (visible behaviour unchanged, decisions logged).
"""

import importlib
import json

import pytest


@pytest.fixture()
def shaping(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.delenv("NEXO_AUTOMATION", raising=False)
    monkeypatch.delenv("NEXO_HEADLESS", raising=False)
    monkeypatch.setenv("NEXO_PROTOCOL_NUDGE_MODE", "shadow")
    monkeypatch.setenv("NEXO_PROTOCOL_NUDGE_THRESHOLD", "3")
    monkeypatch.setenv("NEXO_PROTOCOL_NUDGE_COOLDOWN_S", "300")
    import hook_guardrails
    importlib.reload(hook_guardrails)
    yield hook_guardrails
    importlib.reload(hook_guardrails)


def test_streak_threshold_gates_the_nudge(shaping):
    decisions = [shaping._shape_protocol_nudge("sid-a") for _ in range(4)]
    assert [d["would_emit"] for d in decisions] == [False, False, True, False]
    assert decisions[0]["reason"] == "under-threshold"
    assert decisions[2]["reason"] == "threshold-reached"
    assert decisions[3]["reason"] == "cooldown"


def test_headless_sessions_are_skipped(shaping, monkeypatch):
    monkeypatch.setenv("NEXO_AUTOMATION", "1")
    decision = shaping._shape_protocol_nudge("sid-headless")
    assert decision["would_emit"] is False
    assert decision["reason"] == "headless-covered-by-enforcer"


def test_open_task_resets_the_streak(shaping):
    shaping._shape_protocol_nudge("sid-b")
    shaping._shape_protocol_nudge("sid-b")
    shaping._reset_protocol_nudge_streak("sid-b")
    decision = shaping._shape_protocol_nudge("sid-b")
    assert decision["streak"] == 1
    assert decision["would_emit"] is False


def test_streaks_are_per_session(shaping):
    for _ in range(3):
        shaping._shape_protocol_nudge("sid-c")
    fresh = shaping._shape_protocol_nudge("sid-d")
    assert fresh["streak"] == 1


def test_shadow_log_records_decisions(shaping):
    decision = shaping._shape_protocol_nudge("sid-e")
    shaping._log_protocol_nudge_shadow("sid-e", decision, emitted_today=True)
    log_path = shaping._protocol_nudge_shadow_log_path()
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["sid"] == "sid-e"
    assert rows[-1]["mode"] == "shadow"
    assert rows[-1]["legacy_warning_emitted"] is True


def test_corrupt_state_file_never_breaks_the_hook(shaping):
    path = shaping._protocol_nudge_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    decision = shaping._shape_protocol_nudge("sid-f")
    assert decision["streak"] == 1
