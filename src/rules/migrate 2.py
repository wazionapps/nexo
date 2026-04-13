#!/usr/bin/env python3
"""NEXO Brain Rules Migration System.

Manages versioned core rules that ship with every installation.
Handles adding new rules, removing deprecated ones, and updating
the user's CLAUDE.md without touching their customizations.

Usage:
    from rules.migrate import migrate_rules
    result = migrate_rules(nexo_home)  # Returns dict with changes applied
"""

import json
import os
import re
from pathlib import Path
from typing import Optional


RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core-rules.json")
VERSION_KEY = "rules_version"


def load_core_rules() -> dict:
    """Load the current core rules definition."""
    with open(RULES_FILE, "r") as f:
        return json.load(f)


def get_installed_version(nexo_home: str) -> Optional[str]:
    """Get the rules version currently installed in the user's NEXO home."""
    version_file = os.path.join(nexo_home, "brain", "rules_version.json")
    if not os.path.exists(version_file):
        return None
    try:
        with open(version_file, "r") as f:
            data = json.load(f)
        return data.get("version")
    except (json.JSONDecodeError, KeyError):
        return None


def save_installed_version(nexo_home: str, version: str, rule_ids: list[str]):
    """Record which rules version and rule IDs are installed."""
    version_file = os.path.join(nexo_home, "brain", "rules_version.json")
    os.makedirs(os.path.dirname(version_file), exist_ok=True)
    data = {
        "version": version,
        "installed_rule_ids": rule_ids,
        "installed_at": _now_iso(),
    }
    with open(version_file, "w") as f:
        json.dump(data, f, indent=2)


def get_installed_rule_ids(nexo_home: str) -> list[str]:
    """Get the list of rule IDs currently installed."""
    version_file = os.path.join(nexo_home, "brain", "rules_version.json")
    if not os.path.exists(version_file):
        return []
    try:
        with open(version_file, "r") as f:
            data = json.load(f)
        return data.get("installed_rule_ids", [])
    except (json.JSONDecodeError, KeyError):
        return []


def generate_rules_markdown(rules_data: dict) -> str:
    """Generate the Operational Codex markdown from core-rules.json."""
    lines = [
        "## Operational Codex (NON-NEGOTIABLE)",
        "",
        "These rules are the behavioral foundation of every cognitive co-operator.",
        "They are derived from real production failures and validated through multi-AI debate.",
        f"Rules version: {rules_data['_meta']['version']}",
        "",
    ]

    for cat_key, cat in rules_data["categories"].items():
        lines.append(f"### {cat['label']}")
        lines.append("")
        for rule in cat["rules"]:
            tag = "BLOCKING" if rule["type"] == "blocking" else "ADVISORY"
            lines.append(f"**{rule['id']}. {rule['rule']}** [{tag}]")
            lines.append(f"_{rule['why']}_")
            lines.append("")

    return "\n".join(lines)


def find_codex_section(claude_md: str) -> tuple[int, int]:
    """Find the start and end positions of the Operational Codex section in CLAUDE.md."""
    # Look for the section header
    start_pattern = r"## Operational Codex \(NON-NEGOTIABLE\)"
    start_match = re.search(start_pattern, claude_md)
    if not start_match:
        return (-1, -1)

    start = start_match.start()

    # Find the next ## section header after the codex
    rest = claude_md[start_match.end():]
    next_section = re.search(r"\n## [A-Z]", rest)
    if next_section:
        end = start_match.end() + next_section.start()
    else:
        end = len(claude_md)

    return (start, end)


def migrate_rules(nexo_home: str, dry_run: bool = False) -> dict:
    """Migrate rules to the latest version.

    Compares installed rules version with current core-rules.json.
    Adds new rules, removes deprecated ones, updates CLAUDE.md.

    Args:
        nexo_home: Path to NEXO home directory
        dry_run: If True, show what would change without applying

    Returns:
        Dict with: version_from, version_to, added, removed, unchanged, dry_run
    """
    rules_data = load_core_rules()
    current_version = rules_data["_meta"]["version"]
    installed_version = get_installed_version(nexo_home)
    installed_ids = set(get_installed_rule_ids(nexo_home))

    # Collect all rule IDs from current version
    current_ids = set()
    for cat in rules_data["categories"].values():
        for rule in cat["rules"]:
            current_ids.add(rule["id"])

    # Calculate diff
    added = current_ids - installed_ids if installed_ids else current_ids
    removed = installed_ids - current_ids if installed_ids else set()
    unchanged = current_ids & installed_ids if installed_ids else set()

    result = {
        "version_from": installed_version or "none",
        "version_to": current_version,
        "added": sorted(added),
        "removed": sorted(removed),
        "unchanged": sorted(unchanged),
        "total_rules": len(current_ids),
        "dry_run": dry_run,
    }

    if installed_version == current_version and not added and not removed:
        result["status"] = "up_to_date"
        return result

    if dry_run:
        result["status"] = "changes_pending"
        return result

    # Apply: update the Operational Codex section in CLAUDE.md
    claude_md_path = os.path.join(nexo_home, "CLAUDE.md")
    if os.path.exists(claude_md_path):
        with open(claude_md_path, "r") as f:
            claude_md = f.read()

        new_codex = generate_rules_markdown(rules_data)
        start, end = find_codex_section(claude_md)

        if start >= 0:
            # Replace existing codex section
            claude_md = claude_md[:start] + new_codex + "\n" + claude_md[end:]
        else:
            # Append codex after the first section
            # Find the end of the first ## section
            first_section_end = re.search(r"\n## ", claude_md[10:])
            if first_section_end:
                insert_pos = 10 + first_section_end.start()
                claude_md = claude_md[:insert_pos] + "\n\n" + new_codex + "\n" + claude_md[insert_pos:]
            else:
                claude_md += "\n\n" + new_codex

        with open(claude_md_path, "w") as f:
            f.write(claude_md)

    # Save version record
    save_installed_version(nexo_home, current_version, sorted(current_ids))

    result["status"] = "migrated"
    return result


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python migrate.py <nexo_home> [--dry-run]")
        sys.exit(1)

    home = sys.argv[1]
    dry = "--dry-run" in sys.argv

    result = migrate_rules(home, dry_run=dry)
    print(json.dumps(result, indent=2))
