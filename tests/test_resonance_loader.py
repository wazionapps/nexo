"""v6.0.0 — Validates the new resonance_tiers.json contract.

The loader must produce the canonical 4 tiers × 2 backends shape and the
default tier must be a member of the table. This guards both the shipped
JSON and the expose-for-testing helper ``load_resonance_table``.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import resonance_map as rmap  # noqa: E402


EXPECTED_TIERS = ("maximo", "alto", "medio", "bajo")
EXPECTED_BACKENDS = ("claude_code", "codex")


def test_shipped_tiers_json_matches_contract():
    """Shape guard on the committed JSON — runs before any module import
    side-effects, so a bad merge trips this fast."""
    payload = json.loads((SRC / "resonance_tiers.json").read_text())
    assert isinstance(payload, dict)
    assert payload.get("default_tier") == "alto"
    tiers = payload.get("tiers")
    assert isinstance(tiers, dict)
    for tier in EXPECTED_TIERS:
        assert tier in tiers, f"missing tier {tier!r}"
        for backend in EXPECTED_BACKENDS:
            entry = tiers[tier].get(backend)
            assert isinstance(entry, dict), f"{tier}/{backend} not a dict"
            assert isinstance(entry.get("model"), str) and entry["model"]
            assert "effort" in entry  # may be empty string for future backends


def test_loader_returns_four_tiers_two_backends_eight_pairs():
    table, default_tier = rmap.load_resonance_table()
    assert default_tier in table
    pairs = []
    for tier in EXPECTED_TIERS:
        assert tier in table
        for backend in EXPECTED_BACKENDS:
            assert backend in table[tier]
            model, effort = table[tier][backend]
            assert isinstance(model, str) and model
            assert isinstance(effort, str)
            pairs.append((tier, backend, model, effort))
    assert len(pairs) == 8


def test_loader_raises_when_file_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        rmap.load_resonance_table(path=missing)


def test_loader_rejects_incomplete_tier_list(tmp_path):
    incomplete = tmp_path / "tiers.json"
    incomplete.write_text(json.dumps({
        "tiers": {
            "maximo": {
                "claude_code": {"model": "claude-opus-4-7[1m]", "effort": "max"},
                "codex":       {"model": "gpt-5.4", "effort": "xhigh"},
            }
        },
        "default_tier": "maximo",
    }))
    with pytest.raises(ValueError):
        rmap.load_resonance_table(path=incomplete)


def test_module_global_reflects_json_contents():
    """After v6.0.0 the module-level ``_RESONANCE_TABLE`` must equal what
    the loader returns — no hardcoded table allowed to drift behind it."""
    table_from_loader, _ = rmap.load_resonance_table()
    assert rmap._RESONANCE_TABLE == table_from_loader
