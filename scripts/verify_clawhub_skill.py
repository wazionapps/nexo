#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "clawhub-skill" / "SKILL.md"
PACKAGE_JSON = ROOT / "package.json"


def fail(message: str) -> None:
    raise SystemExit(f"[clawhub-skill] {message}")


def extract(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        fail(f"missing {label}")
    return match.group(1).strip()


def main() -> None:
    skill_text = SKILL_MD.read_text()
    package_text = PACKAGE_JSON.read_text()

    root_version = extract(r'"version":\s*"([^"]+)"', package_text, "root package version")
    skill_version = extract(r"^version:\s*([^\n]+)$", skill_text, "skill version")
    if skill_version != root_version:
        fail(f"skill version {skill_version} does not match root package version {root_version}")

    required_snippets = [
        "metadata:",
        "openclaw:",
        "requires:",
        "install:",
        "kind: node",
        "package: nexo-brain",
        "- nexo",
        "- nexo-brain",
        "~/.nexo/server.py",
        "openclaw gateway restart",
    ]
    for snippet in required_snippets:
        if snippet not in skill_text:
            fail(f"missing expected snippet: {snippet}")

    if "npx clawhub@latest install nexo-brain" in skill_text:
        fail("skill content should not self-reference the marketplace install command")
    if "~/.nexo/src/server.py" in skill_text:
        fail("skill content still points to the obsolete ~/.nexo/src/server.py path")

    print("[clawhub-skill] OK")


if __name__ == "__main__":
    main()
