from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_verify_prod_config_skill_declares_mandatory_live_checks():
    skill_dir = ROOT / "src" / "skills" / "verify-prod-config"
    metadata = json.loads((skill_dir / "skill.json").read_text(encoding="utf-8"))
    guide = (skill_dir / "guide.md").read_text(encoding="utf-8")

    assert metadata["id"] == "SK-VERIFY-PROD-CONFIG"
    assert metadata["mode"] == "guide"
    assert metadata["source_kind"] == "core"
    assert metadata["execution_level"] == "none"

    for trigger in ("DEPLOY-NOTES", "LAUNCH-NOTES", "Stripe production", "SMTP production"):
        assert trigger in metadata["trigger_patterns"]

    for required in (
        "SSH to the production host",
        "real runtime `.env`",
        "Verify through the real provider/API",
        "STALE",
        "last 14 days",
        "secrets redacted",
    ):
        assert required in guide
