from __future__ import annotations

import json
from pathlib import Path


def test_create_nexo_primitive_skill_is_present_with_canonical_links():
    repo_root = Path(__file__).resolve().parents[1]
    skill_dir = repo_root / "src" / "skills" / "create-nexo-primitive"
    skill_json = json.loads((skill_dir / "skill.json").read_text(encoding="utf-8"))
    guide_md = (skill_dir / "guide.md").read_text(encoding="utf-8")
    manual_md = (repo_root / "docs" / "personal-artifacts-manual.md").read_text(encoding="utf-8")

    assert skill_json["id"] == "SK-CREATE-NEXO-PRIMITIVE"
    assert skill_json["source_kind"] == "core"
    assert "create a new skill" in skill_json["trigger_patterns"]
    assert "create a new personal script" in skill_json["trigger_patterns"]
    assert "templates/script-template.py" in guide_md
    assert "docs/personal-artifacts-manual.md" in guide_md
    assert "SK-CREATE-NEXO-PRIMITIVE" in manual_md
