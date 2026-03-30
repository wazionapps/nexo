#!/usr/bin/env python3
"""NEXO Install — First-time setup for NEXO Brain.

Creates ~/.nexo/ directory structure, initializes databases,
copies scripts/hooks/plugins from the repo, and sets NEXO_HOME
in the user's shell profile.

Usage:
    python3 nexo-install.py              # interactive install
    python3 nexo-install.py --yes        # skip confirmations
    python3 nexo-install.py --nexo-home /path  # custom location
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # nexo/src/scripts -> nexo/

# Required directory structure
NEXO_DIRS = [
    "db",
    "brain",
    "logs",
    "operations",
    "coordination",
    "scripts",
    "hooks",
    "plugins",
    "backups",
    "memory",
    "docs",
    "projects",
    "learnings",
    "agents",
    "skills",
]


def get_version() -> str:
    """Read version from package.json."""
    pkg = REPO_ROOT / "package.json"
    if not pkg.exists():
        return "0.0.0"
    return json.loads(pkg.read_text()).get("version", "0.0.0")


def detect_shell() -> tuple[str, Path]:
    """Detect user's shell and return (shell_name, profile_path)."""
    shell = os.environ.get("SHELL", "/bin/bash")
    home = Path.home()

    if "zsh" in shell:
        return "zsh", home / ".zshrc"
    elif "fish" in shell:
        return "fish", home / ".config" / "fish" / "config.fish"
    else:
        # bash — prefer .bashrc, fall back to .bash_profile
        bashrc = home / ".bashrc"
        if bashrc.exists():
            return "bash", bashrc
        return "bash", home / ".bash_profile"


def create_directory_structure(nexo_home: Path):
    """Create all required directories."""
    for d in NEXO_DIRS:
        (nexo_home / d).mkdir(parents=True, exist_ok=True)
    print(f"  Created {len(NEXO_DIRS)} directories in {nexo_home}")


def initialize_databases(nexo_home: Path):
    """Initialize empty databases with schema."""
    os.environ["NEXO_HOME"] = str(nexo_home)
    os.environ["NEXO_SKIP_FS_INDEX"] = "1"

    src_dir = REPO_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    try:
        from db import init_db
        init_db()
        print("  Initialized nexo.db with schema.")
    except Exception as e:
        print(f"  WARNING: DB init error: {e}", file=sys.stderr)
        print("  You can run schema migrations later with nexo-migrate.py")


def copy_repo_files(nexo_home: Path):
    """Copy hooks, scripts, and plugins from the repo."""
    src = REPO_ROOT / "src"

    # Copy scripts
    scripts_src = src / "scripts"
    scripts_dst = nexo_home / "scripts"
    if scripts_src.exists():
        count = 0
        for f in scripts_src.iterdir():
            if f.is_file() and not f.name.startswith("__"):
                shutil.copy2(f, scripts_dst / f.name)
                count += 1
        print(f"  Copied {count} scripts.")

    # Copy plugins
    plugins_src = src / "plugins"
    plugins_dst = nexo_home / "plugins"
    if plugins_src.exists():
        count = 0
        for f in plugins_src.iterdir():
            if f.is_file() and f.suffix == ".py" and not f.name.startswith("__"):
                shutil.copy2(f, plugins_dst / f.name)
                count += 1
        print(f"  Copied {count} plugins.")

    # Copy hooks (templates directory)
    hooks_src = REPO_ROOT / "templates" / "hooks"
    hooks_dst = nexo_home / "hooks"
    if hooks_src.exists():
        count = 0
        for f in hooks_src.iterdir():
            if f.is_file():
                shutil.copy2(f, hooks_dst / f.name)
                count += 1
        if count:
            print(f"  Copied {count} hooks.")


def set_nexo_home_env(nexo_home: Path, shell_name: str, profile_path: Path):
    """Add NEXO_HOME export to shell profile if not already present."""
    export_line = f'export NEXO_HOME="{nexo_home}"'

    if profile_path.exists():
        content = profile_path.read_text()
        if "NEXO_HOME" in content:
            print(f"  NEXO_HOME already set in {profile_path}")
            return
    else:
        content = ""

    with open(profile_path, "a") as f:
        f.write(f"\n# NEXO Brain — cognitive co-operator\n")
        f.write(f"{export_line}\n")

    print(f"  Added NEXO_HOME to {profile_path}")


def write_version_json(nexo_home: Path, version: str):
    """Write version.json."""
    data = {
        "version": version,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "nexo_home": str(nexo_home),
    }
    (nexo_home / "version.json").write_text(json.dumps(data, indent=2) + "\n")
    print(f"  Written version.json (v{version})")


def main():
    parser = argparse.ArgumentParser(description="NEXO Brain — First-time setup")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmations")
    parser.add_argument("--nexo-home", type=str, default=None, help="Custom NEXO_HOME path")
    args = parser.parse_args()

    nexo_home = Path(args.nexo_home) if args.nexo_home else Path.home() / ".nexo"
    version = get_version()
    shell_name, profile_path = detect_shell()

    print(f"NEXO Brain Installer v{version}")
    print(f"=" * 40)
    print(f"  NEXO_HOME: {nexo_home}")
    print(f"  Shell:     {shell_name} ({profile_path})")
    print(f"  Version:   {version}")
    print()

    if nexo_home.exists() and (nexo_home / "version.json").exists():
        existing = json.loads((nexo_home / "version.json").read_text())
        print(f"  Existing installation found: v{existing.get('version', '?')}")
        print(f"  Use nexo-migrate.py to upgrade instead.")
        if not args.yes:
            resp = input("  Continue anyway? (y/N) ")
            if resp.lower() != "y":
                print("Aborted.")
                return

    if not args.yes:
        resp = input("Install NEXO Brain? (Y/n) ")
        if resp.lower() == "n":
            print("Aborted.")
            return

    print()
    print("Installing...")

    # Step 1: Create directories
    create_directory_structure(nexo_home)

    # Step 2: Initialize databases
    initialize_databases(nexo_home)

    # Step 3: Copy files from repo
    copy_repo_files(nexo_home)

    # Step 4: Set NEXO_HOME in shell profile
    set_nexo_home_env(nexo_home, shell_name, profile_path)

    # Step 5: Write version.json
    write_version_json(nexo_home, version)

    print()
    print("=" * 40)
    print("NEXO Brain installed successfully!")
    print()
    print("Next steps:")
    print(f"  1. Restart your shell or run: source {profile_path}")
    print(f"  2. Add the NEXO MCP server to your Claude Code config:")
    print(f"     claude mcp add nexo -- python3 {REPO_ROOT / 'src' / 'server.py'}")
    print(f"  3. Start a Claude Code session — NEXO will initialize automatically.")


if __name__ == "__main__":
    main()
