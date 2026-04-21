from __future__ import annotations

import json
import plistlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CRON_MANIFEST = REPO_ROOT / "src" / "crons" / "manifest.json"
SYNTHESIS_TEMPLATE = REPO_ROOT / "templates" / "launchagents" / "com.nexo.synthesis.plist"
TEMPLATES_README = REPO_ROOT / "templates" / "launchagents" / "README.md"


def test_synthesis_launchagent_template_matches_live_manifest_schedule():
    manifest = json.loads(CRON_MANIFEST.read_text(encoding="utf-8"))
    synthesis = next(item for item in manifest["crons"] if item["id"] == "synthesis")

    assert synthesis["schedule"] == {"hour": 6, "minute": 0}

    plist = plistlib.loads(SYNTHESIS_TEMPLATE.read_bytes())
    assert plist["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert "StartInterval" not in plist

    readme = TEMPLATES_README.read_text(encoding="utf-8")
    assert "| `com.nexo.synthesis.plist` | Daily 06:00 |" in readme
