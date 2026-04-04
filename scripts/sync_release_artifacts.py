#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_PACKAGE_JSON = ROOT / "package.json"
CLAUDE_PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
CLAWHUB_SKILL_MD = ROOT / "clawhub-skill" / "SKILL.md"
OPENCLAW_PACKAGE_JSON = ROOT / "openclaw-plugin" / "package.json"
OPENCLAW_MCP_BRIDGE = ROOT / "openclaw-plugin" / "src" / "mcp-bridge.ts"


def fail(message: str) -> None:
    raise SystemExit(f"[sync-release-artifacts] {message}")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def dump_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def sync_json_version(path: Path, expected_version: str, label: str) -> bool:
    payload = load_json(path)
    if payload.get("version") == expected_version:
        return False
    payload["version"] = expected_version
    dump_json(path, payload)
    print(f"[sync-release-artifacts] synced {label} version -> {expected_version}")
    return True


def sync_clawhub_skill(skill_path: Path, expected_version: str) -> bool:
    text = skill_path.read_text()
    updated = text

    updated = re.sub(
        r"(?m)^version:\s*[^\n]+$",
        f"version: {expected_version}",
        updated,
        count=1,
    )
    updated = updated.replace("~/.nexo/src/server.py", "~/.nexo/server.py")

    if updated == text:
        return False

    skill_path.write_text(updated)
    print(f"[sync-release-artifacts] synced ClawHub skill -> {expected_version}")
    return True


def sync_openclaw_bridge(bridge_path: Path, expected_version: str) -> bool:
    text = bridge_path.read_text()
    updated = text

    updated = updated.replace(
        'resolve(this.config.nexoHome, "src", "server.py")',
        'resolve(this.config.nexoHome, "server.py")',
    )
    updated = re.sub(
        r'clientInfo:\s*\{\s*name:\s*"openclaw-memory-nexo-brain",\s*version:\s*"[^"]+"\s*\}',
        f'clientInfo: {{ name: "openclaw-memory-nexo-brain", version: "{expected_version}" }}',
        updated,
    )

    if updated == text:
        return False

    bridge_path.write_text(updated)
    print(f"[sync-release-artifacts] synced OpenClaw bridge -> {expected_version}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep release-facing integration artifacts in sync.")
    parser.add_argument("--release-version", help="Expected release version (must match root package.json).")
    parser.add_argument("--check", action="store_true", help="Fail if any artifact would change.")
    args = parser.parse_args()

    root_package = load_json(ROOT_PACKAGE_JSON)
    root_version = root_package.get("version")
    if not root_version:
        fail("root package.json is missing version")

    if args.release_version and args.release_version != root_version:
        fail(
            f"release version {args.release_version} does not match root package.json version {root_version}"
        )

    original_payloads = {
        CLAUDE_PLUGIN_JSON: CLAUDE_PLUGIN_JSON.read_text(),
        CLAWHUB_SKILL_MD: CLAWHUB_SKILL_MD.read_text(),
        OPENCLAW_PACKAGE_JSON: OPENCLAW_PACKAGE_JSON.read_text(),
        OPENCLAW_MCP_BRIDGE: OPENCLAW_MCP_BRIDGE.read_text(),
    }

    changed = []
    if sync_json_version(CLAUDE_PLUGIN_JSON, root_version, "Claude plugin"):
        changed.append(".claude-plugin/plugin.json")
    if sync_clawhub_skill(CLAWHUB_SKILL_MD, root_version):
        changed.append("clawhub-skill/SKILL.md")
    if sync_json_version(OPENCLAW_PACKAGE_JSON, root_version, "OpenClaw package"):
        changed.append("openclaw-plugin/package.json")
    if sync_openclaw_bridge(OPENCLAW_MCP_BRIDGE, root_version):
        changed.append("openclaw-plugin/src/mcp-bridge.ts")

    if args.check:
        for path, text in original_payloads.items():
            path.write_text(text)
        if changed:
            fail("artifacts out of sync: " + ", ".join(changed))
        print("[sync-release-artifacts] OK")
        return

    if not changed:
        print("[sync-release-artifacts] already in sync")


if __name__ == "__main__":
    main()
