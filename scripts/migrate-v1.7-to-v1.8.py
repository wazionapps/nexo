#!/usr/bin/env python3
"""
NEXO Migration Script: v1.7.x -> v1.8.0 (Hybrid Architecture)

Migrates CLAUDE.md to the new hybrid architecture where:
- CLAUDE.md = bootstrap (identity, profile, format, autonomy, project atlas)
- MCP instructions field = tool-coupled behavioral rules
- nexo_context_packet = on-demand area-specific context

The MCP server now carries all tool-coupled rules in its `instructions` field,
so CLAUDE.md no longer needs to duplicate them. This reduces CLAUDE.md from
~130 lines to ~50 lines, saving ~3K context tokens per session.

Safe to run multiple times (idempotent). Creates a backup before modifying.

Usage:
    python3 migrate-v1.7-to-v1.8.py [--dry-run] [--nexo-home /path/to/nexo]
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime


# Sections that are now in the MCP instructions field and should be REMOVED from CLAUDE.md
MCP_OWNED_SECTIONS = [
    "Heartbeat",
    "Guard",
    "Delegation",
    "Reminders & Followups",
    "Reminders y Followups",
    "Memory",
    "Memoria",
    "Trust Score",
    "Adaptive Mode",
    "Dissonance",
    "Disonancia",
    "Observe the User",
    "Observar a {{user}}",  # legacy personal CLAUDE.md files
    "Observar al Usuario",
    "Change Log",
    "Session Diary",
    "Cortex",
    "Operational Codex",
]

# Sections that STAY in CLAUDE.md (bootstrap layer)
BOOTSTRAP_SECTIONS = [
    "Startup",
    "User Profile",
    "{{user_name}}",  # legacy personal CLAUDE.md files
    "Formato",
    "Format",
    "Autonomy",
    "Autonomía",
    "Project Atlas",
    "Atlas de Proyectos",
    "Hooks",
    "Menu",
    "Platforms",
    "Plataformas",
    "Repo",
]


def find_nexo_home(override=None):
    if override:
        return override
    candidates = [
        os.path.expanduser("~/nexo"),
        os.path.expanduser("~/.nexo"),
        os.path.expanduser("~/claude/nexo-mcp"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def find_claude_md():
    """Find the CLAUDE.md file that contains NEXO instructions."""
    candidates = [
        os.path.expanduser("~/.claude/CLAUDE.md"),
        os.path.expanduser("~/CLAUDE.md"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def parse_sections(content):
    """Parse markdown into sections by ## headers."""
    sections = []
    current_header = None
    current_lines = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_header is not None:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_header is not None:
        sections.append((current_header, "\n".join(current_lines)))
    elif current_lines:
        sections.append(("_preamble", "\n".join(current_lines)))

    return sections


def is_mcp_owned(header):
    """Check if a section header matches an MCP-owned section."""
    header_lower = header.lower()
    for section in MCP_OWNED_SECTIONS:
        if section.lower() in header_lower:
            return True
    return False


def migrate_claude_md(path, dry_run=False):
    """Slim down CLAUDE.md by removing MCP-owned sections."""
    with open(path, "r") as f:
        content = f.read()

    original_lines = len(content.strip().split("\n"))
    sections = parse_sections(content)

    # Separate preamble, bootstrap, and MCP-owned sections
    preamble = ""
    kept = []
    removed = []

    for header, body in sections:
        if header == "_preamble":
            preamble = body
        elif is_mcp_owned(header):
            removed.append(header)
        else:
            kept.append((header, body))

    if not removed:
        print("  CLAUDE.md already migrated (no MCP-owned sections found).")
        return False

    # Add hybrid architecture note to preamble
    if "MCP instructions" not in preamble and "instructions" not in preamble:
        preamble = preamble.rstrip() + (
            "\nTool-coupled behavioral rules (heartbeat, guard, trust, memory, diary) "
            "now live in the MCP server instructions field and are injected automatically.\n"
        )

    # Reconstruct
    new_content = preamble + "\n"
    for header, body in kept:
        new_content += "\n" + body + "\n"

    new_lines = len(new_content.strip().split("\n"))

    print(f"  Sections removed (now in MCP): {', '.join(removed)}")
    print(f"  Lines: {original_lines} → {new_lines} (saved {original_lines - new_lines})")

    if dry_run:
        print("  [DRY RUN] No changes written.")
        return True

    # Backup
    backup = path + f".backup-{datetime.now().strftime(\"%Y%m%d-%H%M%S\")}"
    shutil.copy2(path, backup)
    print(f"  Backup: {backup}")

    with open(path, "w") as f:
        f.write(new_content)
    print("  CLAUDE.md updated.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate NEXO to v1.8 hybrid architecture")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying files")
    parser.add_argument("--nexo-home", help="Override NEXO home directory")
    args = parser.parse_args()

    print("NEXO v1.7 → v1.8 Migration (Hybrid Architecture)")
    print("=" * 50)
    print()

    # Step 1: Find and migrate CLAUDE.md
    claude_md = find_claude_md()
    if claude_md:
        print(f"Found CLAUDE.md: {claude_md}")
        migrate_claude_md(claude_md, dry_run=args.dry_run)
    else:
        print("No CLAUDE.md found (skipping).")

    print()
    print("Migration complete.")
    print()
    print("What changed:")
    print("  - CLAUDE.md now contains only bootstrap (identity, format, autonomy)")
    print("  - Tool-coupled rules are in the MCP server instructions field")
    print("  - Context-specific rules load on-demand via nexo_context_packet")
    print()
    print("The MCP server must be restarted for instructions to take effect.")


if __name__ == "__main__":
    main()
