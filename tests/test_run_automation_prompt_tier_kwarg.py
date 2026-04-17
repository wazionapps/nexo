"""v6.0.2 — run_automation_prompt accepts tier= and propagates it through
the resolver so personal/* callers can pin a resonance per call.

We exercise the resolver path directly to avoid spinning up a real
``claude`` subprocess: the test confirms that the tier chosen by
``resolve_model_and_effort`` matches the expected (model, effort) pair
for the relevant tier and backend.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import resonance_map as rmap  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_preferences(monkeypatch):
    monkeypatch.setattr(rmap, "_load_user_default_resonance", lambda: "")


def test_tier_medio_resolves_to_claude_code_high_effort():
    """Mirrors what run_automation_prompt passes downstream: tier='medio'
    must produce the tier-medio entry from resonance_tiers.json for the
    claude_code backend."""
    model, effort = rmap.resolve_model_and_effort(
        "personal/test", "claude_code", explicit_tier="medio"
    )
    assert effort == "high"
    assert "claude-opus-4-7" in model


def test_reasoning_effort_explicit_overrides_resolver():
    """run_automation_prompt keeps an explicit ``reasoning_effort`` kwarg
    that bypasses the resolver-provided effort. We verify the resolver
    still returns its own mapping so the caller's override is an
    in-process swap, not a silent loss of telemetry."""
    model, effort = rmap.resolve_model_and_effort(
        "personal/test", "claude_code", explicit_tier="alto"
    )
    # Resolver returns alto's xhigh; the agent_runner later overwrites
    # ``reasoning_effort`` locally when the caller passed one explicitly.
    assert effort == "xhigh"


def test_no_tier_and_no_user_default_uses_alto():
    model, effort = rmap.resolve_model_and_effort("personal/test", "claude_code")
    assert (model, effort) == rmap._RESONANCE_TABLE["alto"]["claude_code"]


def test_agent_runner_signature_accepts_tier_kwarg():
    """Regression guard — the kwarg must appear in the function signature
    so older callers that do not pass it still work via default."""
    import inspect
    from agent_runner import run_automation_prompt, run_automation_interactive

    prompt_sig = inspect.signature(run_automation_prompt)
    interactive_sig = inspect.signature(run_automation_interactive)
    assert "tier" in prompt_sig.parameters
    assert prompt_sig.parameters["tier"].default == ""
    assert "tier" in interactive_sig.parameters
    assert interactive_sig.parameters["tier"].default == ""
