"""v6.0.3 — migration that relocates resonance_tiers.json.

Before v6.0.3 the installer wrote the file to NEXO_HOME root; v6.0.3 moves
it to the public contract path NEXO_HOME/brain/. This migration reconciles
existing runtimes during ``nexo update``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import auto_update  # noqa: E402


@pytest.fixture
def fake_home(tmp_path):
    (tmp_path / "brain").mkdir()
    return tmp_path


def _sample_payload() -> dict:
    return {
        "default_tier": "alto",
        "tiers": {
            "maximo": {"claude_code": {"model": "x", "effort": "high"}},
            "alto": {"claude_code": {"model": "x", "effort": "medium"}},
        },
    }


def test_relocates_legacy_file_when_brain_empty(fake_home):
    legacy = fake_home / "resonance_tiers.json"
    contract = fake_home / "brain" / "resonance_tiers.json"
    payload = _sample_payload()
    legacy.write_text(json.dumps(payload))
    assert not contract.exists()

    actions = auto_update._relocate_resonance_tiers_contract(fake_home)

    assert contract.is_file()
    assert not legacy.exists()
    assert json.loads(contract.read_text()) == payload
    assert "resonance-contract-relocate:moved-to-brain" in actions


def test_drops_legacy_when_brain_already_has_contract(fake_home):
    legacy = fake_home / "resonance_tiers.json"
    contract = fake_home / "brain" / "resonance_tiers.json"
    legacy.write_text(json.dumps({"stale": True}))
    contract.write_text(json.dumps(_sample_payload()))

    actions = auto_update._relocate_resonance_tiers_contract(fake_home)

    assert contract.is_file()
    assert not legacy.exists(), "legacy file must be unlinked"
    assert json.loads(contract.read_text()) == _sample_payload(), (
        "must not overwrite the authoritative contract with stale legacy"
    )
    assert "resonance-contract-relocate:legacy-removed" in actions


def test_idempotent_when_only_contract_exists(fake_home):
    contract = fake_home / "brain" / "resonance_tiers.json"
    contract.write_text(json.dumps(_sample_payload()))

    actions = auto_update._relocate_resonance_tiers_contract(fake_home)

    assert contract.is_file()
    assert actions == []


def test_noop_when_neither_exists(fake_home):
    # Both legacy and contract are absent — the JS installer will write
    # the file on the next pass; Python migration has nothing to do.
    contract = fake_home / "brain" / "resonance_tiers.json"
    legacy = fake_home / "resonance_tiers.json"
    assert not contract.exists() and not legacy.exists()

    actions = auto_update._relocate_resonance_tiers_contract(fake_home)

    assert actions == []
    assert not contract.exists()


def test_migration_never_raises_on_unreadable_brain(fake_home, monkeypatch):
    # Even if mkdir fails mid-migration, the function must return actions
    # and never propagate an exception (update must not block).
    def boom(*_a, **_kw):
        raise PermissionError("brain dir not writable")

    monkeypatch.setattr(auto_update.Path, "mkdir", boom)

    actions = auto_update._relocate_resonance_tiers_contract(fake_home)
    assert any(a.startswith("resonance-contract-relocate-warning:mkdir:") for a in actions)
