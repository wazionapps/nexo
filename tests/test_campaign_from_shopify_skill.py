import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "src" / "skills" / "campaign-from-shopify"


def test_campaign_from_shopify_skill_declares_required_workflow():
    metadata = json.loads((SKILL_DIR / "skill.json").read_text(encoding="utf-8"))
    guide = (SKILL_DIR / "guide.md").read_text(encoding="utf-8")

    assert metadata["id"] == "SK-GADS-CAMPAIGN-FROM-SHOPIFY"
    assert metadata["source_kind"] == "core"
    assert metadata["mode"] == "guide"
    assert "Google Ads desde Shopify" in metadata["trigger_patterns"]
    assert "complete collection inventory" in guide
    assert "HTTP 200" in guide
    assert "validate_only=true" in guide
    assert "PAUSED" in guide
    assert "GAQL" in guide
    assert "Never activate directly from a generated draft" in guide
