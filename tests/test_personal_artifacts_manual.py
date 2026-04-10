"""Regression checks for the canonical personal artifacts manual."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL = REPO_ROOT / "docs" / "personal-artifacts-manual.md"


def test_manual_has_operator_decision_matrix_and_runtime_anchors():
    text = MANUAL.read_text(encoding="utf-8")

    assert "## Decision Tree" in text
    assert "### Quick Selection Table" in text
    assert "## Source Anchors" in text
    assert "src/script_registry.py" in text
    assert "src/plugin_loader.py" in text


def test_manual_blocks_known_bad_schedule_patterns_and_manual_plists():
    text = MANUAL.read_text(encoding="utf-8")

    assert "`monthly:1`" in text
    assert "Do not invent `monthly:1`" in text
    assert "LaunchAgent" in text
    assert "nexo scripts reconcile" in text
    assert "real runner" in text


def test_manual_is_generic_not_bound_to_francisco_machine():
    text = MANUAL.read_text(encoding="utf-8")

    assert "NEXO_HOME" in text
    assert "/Users/franciscoc" not in text
