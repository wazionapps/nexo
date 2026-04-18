"""Plan 0.X.5 — artifact_class entries in entities_universal.json preset.

Ensures the preset covers the four canonical artifact classes the Plan
Consolidado requires (github_issue, nexo_release, email_to_operator_contact,
shopify_banner_block, changelog_entry) and that every artifact_class entry
has the fields Guardian R15/R22 depend on (type, name, metadata).
"""

import json
from pathlib import Path


PRESET = Path(__file__).resolve().parents[1] / "src" / "presets" / "entities_universal.json"


def _load_preset():
    return json.loads(PRESET.read_text(encoding="utf-8"))


def test_preset_parses_as_json():
    data = _load_preset()
    assert data.get("version")
    assert "entities" in data
    assert isinstance(data["entities"], list)


def test_artifact_class_entries_cover_required_names():
    data = _load_preset()
    artifact_names = {
        e["name"]
        for e in data["entities"]
        if e.get("type") == "artifact_class"
    }
    required = {
        "github_issue",
        "nexo_release",
        "email_to_operator_contact",
        "shopify_banner_block",
        "changelog_entry",
    }
    missing = required - artifact_names
    assert not missing, f"missing artifact_class entries: {sorted(missing)}"


def test_every_artifact_class_has_metadata_shape():
    data = _load_preset()
    for entity in data["entities"]:
        if entity.get("type") != "artifact_class":
            continue
        assert entity.get("name"), entity
        md = entity.get("metadata")
        assert isinstance(md, dict), f"{entity['name']} metadata must be dict"
        assert md, f"{entity['name']} metadata must be non-empty"
