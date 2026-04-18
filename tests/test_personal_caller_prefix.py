"""v6.0.2 — personal/* caller prefix and explicit_tier kwarg.

Verifies the resolver bypasses the registry for ``personal/*`` callers and
follows the documented precedence (explicit_tier → user_default →
default_resonance pref → DEFAULT_RESONANCE). Registered callers keep
v6.0.0 behaviour.
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
    """Neutralise the host's on-disk calibration so every assertion in this
    module exercises the resolver, not the operator's real ``default_resonance``.
    """
    monkeypatch.setattr(rmap, "_load_user_default_resonance", lambda: "")


def test_explicit_tier_wins_for_personal_caller():
    assert rmap.resolve_tier_for_caller("personal/foo", explicit_tier="maximo") == "maximo"


def test_user_default_used_when_no_explicit_tier():
    assert rmap.resolve_tier_for_caller("personal/foo", user_default="medio") == "medio"


def test_falls_back_to_default_resonance_when_nothing_provided():
    assert rmap.resolve_tier_for_caller("personal/foo") == rmap.DEFAULT_RESONANCE == "alto"


def test_invalid_explicit_tier_falls_through_without_crash():
    assert rmap.resolve_tier_for_caller("personal/foo", explicit_tier="garbage") == rmap.DEFAULT_RESONANCE


def test_explicit_tier_beats_user_default():
    assert (
        rmap.resolve_tier_for_caller(
            "personal/foo", explicit_tier="medio", user_default="bajo"
        )
        == "medio"
    )


def test_unregistered_non_personal_caller_still_raises():
    with pytest.raises(rmap.UnregisteredCallerError):
        rmap.resolve_tier_for_caller("nonexistent/caller")


def test_registered_caller_unchanged_backcompat():
    """agent_run/generic resolves via the registry, ignoring explicit_tier
    (that kwarg only applies to the personal/* branch)."""
    tier = rmap.resolve_tier_for_caller("agent_run/generic")
    assert tier in rmap.TIERS
    assert tier == rmap.SYSTEM_OWNED_CALLERS["agent_run/generic"]


def test_resolve_model_and_effort_honours_explicit_tier():
    model, effort = rmap.resolve_model_and_effort(
        "personal/foo", "claude_code", explicit_tier="maximo"
    )
    assert (model, effort) == rmap._RESONANCE_TABLE["maximo"]["claude_code"]
