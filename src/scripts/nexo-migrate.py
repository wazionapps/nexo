#!/usr/bin/env python3
"""NEXO Migration Tool — automatic, idempotent upgrades between versions.

Usage:
    python3 nexo-migrate.py              # auto-detect current → target
    python3 nexo-migrate.py --dry-run    # show what would happen
    python3 nexo-migrate.py --from 1.6.0 # override detected current version

Reads current version from $NEXO_HOME/version.json.
Reads target version from the repo's package.json.
Backs up NEXO_HOME/db/ before any migration.
Runs DB schema migrations via the existing _schema.py system.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", Path.home() / ".nexo"))
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # nexo/src/scripts -> nexo/


# ── Version helpers ──────────────────────────────────────────────

def parse_version(v: str) -> tuple:
    """Parse '1.7.0-beta.1' → (1, 7, 0, 'beta.1'). Pre-release is optional."""
    parts = v.strip().lstrip("v").split("-", 1)
    nums = tuple(int(x) for x in parts[0].split("."))
    pre = parts[1] if len(parts) > 1 else ""
    return (*nums, pre)


def version_key(v: str) -> tuple:
    """Sortable key: releases sort after pre-releases of same version."""
    nums = parse_version(v)
    # Empty pre-release string sorts AFTER any pre-release tag
    pre = nums[3] if len(nums) > 3 else ""
    return (nums[0], nums[1], nums[2], 0 if pre else 1, pre)


def get_current_version() -> str:
    """Read installed version from NEXO_HOME/version.json."""
    vfile = NEXO_HOME / "version.json"
    if not vfile.exists():
        return "0.0.0"
    try:
        data = json.loads(vfile.read_text())
        return data.get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def get_target_version() -> str:
    """Read target version from repo package.json."""
    pkg = REPO_ROOT / "package.json"
    if not pkg.exists():
        print(f"ERROR: package.json not found at {pkg}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(pkg.read_text())
    return data["version"]


# ── Backup ───────────────────────────────────────────────────────

def backup_databases() -> str:
    """Backup all .db files before migration. Returns backup dir path."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = NEXO_HOME / "backups" / f"pre-migrate-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    data_dir = NEXO_HOME / "data"
    if data_dir.exists():
        for db_file in data_dir.glob("*.db*"):
            shutil.copy2(db_file, backup_dir / db_file.name)
    # Also check legacy db/ location
    legacy_db_dir = NEXO_HOME / "db"
    if legacy_db_dir.exists():
        for db_file in legacy_db_dir.glob("*.db*"):
            if not (backup_dir / db_file.name).exists():
                shutil.copy2(db_file, backup_dir / db_file.name)

    # Also backup version.json
    vfile = NEXO_HOME / "version.json"
    if vfile.exists():
        shutil.copy2(vfile, backup_dir / "version.json")

    return str(backup_dir)


# ── Migration steps ──────────────────────────────────────────────

def ensure_nexo_home_dirs():
    """Create all required NEXO_HOME subdirectories."""
    dirs = [
        "db", "brain", "logs", "operations", "coordination",
        "scripts", "hooks", "plugins", "backups", "memory",
        "docs", "projects", "learnings", "agents", "skills",
    ]
    for d in dirs:
        (NEXO_HOME / d).mkdir(parents=True, exist_ok=True)


def run_db_schema_migrations():
    """Run the formal DB schema migration system from _schema.py."""
    # Add src/ to path so we can import the db module
    src_dir = REPO_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Set NEXO_HOME env for the db module
    os.environ["NEXO_HOME"] = str(NEXO_HOME)
    os.environ["NEXO_SKIP_FS_INDEX"] = "1"  # Don't rebuild FTS during migration

    try:
        from db import init_db
        init_db()
        print("  DB schema migrations applied.")
    except Exception as e:
        print(f"  WARNING: DB schema migration error: {e}", file=sys.stderr)


def write_version_json(version: str):
    """Write version.json with the installed version."""
    vfile = NEXO_HOME / "version.json"
    data = {
        "version": version,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "nexo_home": str(NEXO_HOME),
    }
    vfile.write_text(json.dumps(data, indent=2) + "\n")


# ── Migration registry ───────────────────────────────────────────
# Each entry: version → list of (description, callable)
# Migrations run for all versions > current AND <= target.

def _migrate_1_7_0():
    """1.7.0: Ensure NEXO_HOME paths, create directories, update version."""
    ensure_nexo_home_dirs()
    run_db_schema_migrations()
    print("  Created/verified all NEXO_HOME directories.")


MIGRATION_REGISTRY: dict[str, list[tuple[str, callable]]] = {
    "1.7.0": [
        ("Ensure NEXO_HOME dirs + DB schema", _migrate_1_7_0),
    ],
}


# ── Main ─────────────────────────────────────────────────────────

def get_applicable_migrations(current: str, target: str) -> list[tuple[str, str, callable]]:
    """Return list of (version, description, fn) for migrations between current and target."""
    current_key = version_key(current)
    target_key = version_key(target)

    applicable = []
    for ver, steps in sorted(MIGRATION_REGISTRY.items(), key=lambda x: version_key(x[0])):
        ver_key = version_key(ver)
        # Run if version > current and <= target (base version comparison)
        base_ver = ver.split("-")[0]  # strip pre-release for comparison
        base_ver_key = version_key(base_ver)
        if base_ver_key > (current_key[0], current_key[1], current_key[2], current_key[3], current_key[4] if len(current_key) > 4 else ""):
            if base_ver_key <= (target_key[0], target_key[1], target_key[2], 1, ""):
                for desc, fn in steps:
                    applicable.append((ver, desc, fn))

    return applicable


def main():
    parser = argparse.ArgumentParser(description="NEXO Migration Tool")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    parser.add_argument("--from", dest="from_ver", help="Override detected current version")
    args = parser.parse_args()

    current = args.from_ver or get_current_version()
    target = get_target_version()

    print(f"NEXO Migration: {current} → {target}")
    print(f"NEXO_HOME: {NEXO_HOME}")
    print()

    if version_key(current) >= version_key(target):
        print("Already up to date. Nothing to migrate.")
        return

    migrations = get_applicable_migrations(current, target)
    if not migrations:
        print("No migration steps needed (only version bump).")
    else:
        print(f"Migrations to run ({len(migrations)}):")
        for ver, desc, _ in migrations:
            print(f"  [{ver}] {desc}")
        print()

    if args.dry_run:
        print("DRY RUN — no changes made.")
        return

    # Backup before anything
    backup_path = backup_databases()
    print(f"Backup created: {backup_path}")
    print()

    # Ensure base directories exist
    ensure_nexo_home_dirs()

    # Run migrations
    for ver, desc, fn in migrations:
        print(f"Running [{ver}] {desc}...")
        try:
            fn()
            print(f"  Done.")
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            print(f"  Backup at: {backup_path}", file=sys.stderr)
            sys.exit(1)

    # Write final version
    write_version_json(target)
    print(f"\nMigration complete: {current} → {target}")


if __name__ == "__main__":
    main()
