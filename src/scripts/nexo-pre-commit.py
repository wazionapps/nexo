#!/usr/bin/env /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
"""
NEXO Pre-Commit Validator — Dynamic version
Queries nexo.db learnings to surface relevant warnings/blockers before commit.
Runs standalone (no MCP dependency, just sqlite3).
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

NEXO_DB = Path.home() / ".nexo" / "nexo-mcp" / "nexo.db"


def get_staged_files():
    """Get list of staged files."""
    result = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        capture_output=True, text=True
    )
    return [f for f in result.stdout.strip().split('\n') if f]


def check_file(filepath, conn):
    """Dynamic checks from learnings DB."""
    errors, warnings = [], []

    filename = Path(filepath).name
    parent_dir = Path(filepath).parent.name

    # 1. Find learnings mentioning this file or directory
    file_learnings = conn.execute(
        "SELECT id, title, content FROM learnings WHERE INSTR(content, ?) > 0 OR INSTR(content, ?) > 0",
        (filename, parent_dir)
    ).fetchall()

    # 2. Check repetition count for each matching learning
    for row in file_learnings:
        lid, title = row[0], row[1]
        rep_count = conn.execute(
            "SELECT COUNT(*) FROM error_repetitions WHERE original_learning_id = ?",
            (lid,)
        ).fetchone()[0]

        if rep_count >= 5:
            errors.append(f"BLOCKED #{lid} ({rep_count}x repeated): {title[:80]}")
        elif rep_count >= 3:
            warnings.append(f"WARNING #{lid} ({rep_count}x repeated): {title[:80]}")

    # 3. Universal rules (SIEMPRE/NUNCA/ANTES) matching this file
    universal = conn.execute(
        "SELECT id, title, content FROM learnings WHERE "
        "(content LIKE '%SIEMPRE%' OR content LIKE '%NUNCA%' OR content LIKE '%ANTES%') "
        "AND (INSTR(content, ?) > 0 OR INSTR(content, ?) > 0)",
        (filename, parent_dir)
    ).fetchall()

    for row in universal:
        lid, title = row[0], row[1]
        # Don't duplicate if already found above
        if not any(f"#{lid}" in e for e in errors + warnings):
            warnings.append(f"RULE #{lid}: {title[:80]}")

    return errors, warnings


def main():
    """Run pre-commit validation."""
    staged = get_staged_files()
    if not staged:
        print("No staged files to check")
        return 0

    if not NEXO_DB.exists():
        print("nexo.db not found — skipping dynamic checks")
        return 0

    conn = sqlite3.connect(str(NEXO_DB), timeout=5)

    all_errors = {}
    all_warnings = {}

    for filepath in staged:
        errors, warnings = check_file(filepath, conn)
        if errors:
            all_errors[filepath] = errors
        if warnings:
            all_warnings[filepath] = warnings

    conn.close()

    # Print warnings (non-blocking)
    if all_warnings:
        print("\nWARNINGS:")
        for filepath, warns in all_warnings.items():
            print(f"\n  {filepath}:")
            for w in warns:
                print(f"    - {w}")

    # Print errors (blocking)
    if all_errors:
        print("\nBLOCKED — Fix these before committing:\n")
        for filepath, errs in all_errors.items():
            print(f"  {filepath}:")
            for e in errs:
                print(f"    - {e}")
        print()
        return 1

    if all_warnings:
        print("\nPre-commit passed with warnings\n")
    else:
        print("Pre-commit validation passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
