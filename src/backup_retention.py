"""Machine-readable backup retention contracts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import paths


def _pruner_script() -> Path:
    script = Path(__file__).resolve().parent / "scripts" / "prune_runtime_backups.py"
    if script.is_file():
        return script
    fallback = paths.core_scripts_dir() / "prune_runtime_backups.py"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError("prune_runtime_backups.py not found")


def _run_pruner(
    *,
    root: Path | None = None,
    apply: bool = False,
    max_bytes: str | int | None = None,
    delete_all_technical: bool = False,
) -> dict:
    command = [
        sys.executable,
        str(_pruner_script()),
        "--root",
        str(root or paths.backups_dir()),
        "--json",
    ]
    if apply:
        command.append("--apply")
    if max_bytes is not None:
        command.extend(["--max-bytes", str(max_bytes)])
    if delete_all_technical:
        command.append("--delete-all-technical")
    proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"raw_stdout": proc.stdout}
    payload["ok"] = proc.returncode == 0
    payload["returncode"] = proc.returncode
    payload["stderr"] = proc.stderr[-2000:]
    return payload


def backup_retention_plan(*, root: Path | None = None, max_bytes: str | int | None = None) -> dict:
    """Return a deterministic dry-run retention plan."""
    return _run_pruner(root=root, max_bytes=max_bytes, apply=False)


def backup_retention_apply(
    *,
    root: Path | None = None,
    max_bytes: str | int | None = None,
    delete_all_technical: bool = False,
) -> dict:
    """Apply backup retention with the pruner's restore-point guard enabled."""
    return _run_pruner(
        root=root,
        max_bytes=max_bytes,
        apply=True,
        delete_all_technical=delete_all_technical,
    )
