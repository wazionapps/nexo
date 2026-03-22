#!/usr/bin/env python3
"""
NEXO Auto-Update — checks for new versions and applies updates.

Runs at boot via the catch-up system or manually.
Compares local version with the latest GitHub release.
If a new version is available, downloads and applies the update.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
VERSION_FILE = NEXO_HOME / "version.json"
LOG_FILE = NEXO_HOME / "logs" / "auto-update.log"
REPO = "wazionapps/nexo"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_local_version() -> str:
    """Read locally installed version."""
    if VERSION_FILE.exists():
        try:
            return json.loads(VERSION_FILE.read_text()).get("version", "0.0.0")
        except Exception:
            pass
    return "0.0.0"


def get_remote_version() -> dict | None:
    """Check latest release on GitHub."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{REPO}/releases/latest"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                "version": data.get("tag_name", "").lstrip("v"),
                "tarball_url": data.get("tarball_url", ""),
                "published_at": data.get("published_at", ""),
                "body": data.get("body", "")[:500],
            }
    except Exception:
        pass
    return None


def version_compare(local: str, remote: str) -> int:
    """Compare semver strings. Returns -1 (local older), 0 (same), 1 (local newer)."""
    def parse(v):
        parts = v.split(".")
        return tuple(int(p) for p in parts if p.isdigit())

    l, r = parse(local), parse(remote)
    if l < r:
        return -1
    elif l > r:
        return 1
    return 0


def apply_update(tarball_url: str, new_version: str) -> bool:
    """Download and apply update from GitHub release tarball."""
    import tempfile

    log(f"Downloading update v{new_version}...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="nexo-update-"))

    try:
        # Download tarball
        tarball = tmp_dir / "release.tar.gz"
        result = subprocess.run(
            ["curl", "-sL", "-o", str(tarball), tarball_url],
            capture_output=True, timeout=60
        )
        if result.returncode != 0:
            log(f"Download failed: {result.stderr}")
            return False

        # Extract
        subprocess.run(
            ["tar", "xzf", str(tarball), "-C", str(tmp_dir)],
            capture_output=True, timeout=30
        )

        # Find extracted directory (GitHub tarballs have a top-level dir)
        extracted = [d for d in tmp_dir.iterdir() if d.is_dir()]
        if not extracted:
            log("No directory found in tarball")
            return False

        src_dir = extracted[0] / "src"
        if not src_dir.exists():
            log("No src/ directory in release")
            return False

        # Backup current files
        backup_dir = NEXO_HOME / "backups" / f"pre-update-{new_version}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        files_updated = 0
        # Update core Python files
        for py_file in src_dir.glob("*.py"):
            dest = NEXO_HOME / py_file.name
            if dest.exists():
                shutil.copy2(dest, backup_dir / py_file.name)
            shutil.copy2(py_file, dest)
            files_updated += 1

        # Update plugins
        plugins_src = src_dir / "plugins"
        if plugins_src.exists():
            plugins_dest = NEXO_HOME / "plugins"
            plugins_dest.mkdir(exist_ok=True)
            for py_file in plugins_src.glob("*.py"):
                dest = plugins_dest / py_file.name
                if dest.exists():
                    shutil.copy2(dest, backup_dir / f"plugin_{py_file.name}")
                shutil.copy2(py_file, dest)
                files_updated += 1

        # Update scripts
        scripts_src = src_dir / "scripts"
        if scripts_src.exists():
            scripts_dest = NEXO_HOME / "scripts"
            scripts_dest.mkdir(exist_ok=True)
            for py_file in scripts_src.glob("*.py"):
                dest = scripts_dest / py_file.name
                if dest.exists():
                    shutil.copy2(dest, backup_dir / f"script_{py_file.name}")
                shutil.copy2(py_file, dest)
                files_updated += 1

        # Update hooks
        hooks_src = src_dir / "hooks"
        if hooks_src.exists():
            hooks_dest = NEXO_HOME / "hooks"
            hooks_dest.mkdir(exist_ok=True)
            for sh_file in hooks_src.glob("*.sh"):
                dest = hooks_dest / sh_file.name
                shutil.copy2(sh_file, dest)
                os.chmod(dest, 0o755)
                files_updated += 1

        # Save new version
        VERSION_FILE.write_text(json.dumps({
            "version": new_version,
            "updated_at": datetime.now().isoformat(),
            "files_updated": files_updated,
            "backup": str(backup_dir),
        }, indent=2))

        log(f"Update applied: {files_updated} files updated. Backup at {backup_dir}")
        return True

    except Exception as e:
        log(f"Update failed: {e}")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    log("=== NEXO Auto-Update check ===")

    local_ver = get_local_version()
    log(f"Local version: {local_ver}")

    remote = get_remote_version()
    if not remote:
        log("Could not check remote version (no network or no releases)")
        return

    remote_ver = remote["version"]
    log(f"Remote version: {remote_ver}")

    cmp = version_compare(local_ver, remote_ver)
    if cmp >= 0:
        log("Already up to date.")
        return

    log(f"Update available: {local_ver} → {remote_ver}")
    log(f"Release notes: {remote['body'][:200]}")

    if remote["tarball_url"]:
        success = apply_update(remote["tarball_url"], remote_ver)
        if success:
            log(f"Successfully updated to v{remote_ver}")
        else:
            log("Update failed — will retry next boot")
    else:
        log("No tarball URL in release — manual update needed")

    log("=== Done ===")


if __name__ == "__main__":
    main()
