"""NEXO Recover plugin — restore a wiped nexo.db from the hourly backup stream.

Exposed as MCP tool ``nexo_recover`` and CLI subcommand ``nexo recover``.

Flow:
    1. List available backups (hourly ``nexo-YYYY-MM-DD-HHMM.db``, and
       the pre-update / pre-heal snapshots if present), sorted newest-first.
    2. Pick a source — either the most recent hourly backup that passes the
       row-count floor, or the one the caller specified via --from.
    3. Kill any live NEXO MCP servers so a running process cannot clobber the
       restored file on the next write.
    4. Snapshot the current nexo.db to ``backups/pre-recover-<ts>/`` so the
       recovery itself is reversible.
    5. Copy the chosen backup over ``data/nexo.db`` using sqlite3.backup and
       validate row counts match.
"""

from __future__ import annotations

import json
import os
import paths
import re
import sys
import time
from pathlib import Path

try:
    from runtime_home import export_resolved_nexo_home
except ImportError:  # pragma: no cover - happens only if runtime_home removed
    def export_resolved_nexo_home() -> Path:
        return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

from db_guard import (
    CRITICAL_TABLES,
    EMPTY_DB_SIZE_BYTES,
    HOURLY_BACKUP_GLOB,
    MIN_REFERENCE_ROWS,
    WIPE_THRESHOLD_PCT,
    db_looks_wiped,
    db_row_counts,
    diff_row_counts,
    find_latest_hourly_backup,
    quiesce_nexo_db_writers,
    resume_nexo_launchagents,
    safe_sqlite_backup,
    validate_backup_matches_source,
)

# Path resolution moved to lazy helpers (AUDITOR-V700-PASS2 §11, B10 item 3)
# to keep monkeypatched NEXO_HOME / paths.* fixtures honoured. PEP 562
# ``__getattr__`` below preserves the legacy constant names for any caller
# that imports them as module attributes.


def _nexo_home() -> Path:
    return export_resolved_nexo_home()


def _data_dir() -> Path:
    return paths.data_dir()


def _backup_base() -> Path:
    return paths.backups_dir()


def _primary_db() -> Path:
    return _data_dir() / "nexo.db"


_LAZY_PATHS = {
    "NEXO_HOME": _nexo_home,
    "DATA_DIR": _data_dir,
    "BACKUP_BASE": _backup_base,
    "PRIMARY_DB": _primary_db,
}


def __getattr__(name: str):
    resolver = _LAZY_PATHS.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return resolver()


# ── Backup discovery ────────────────────────────────────────────────────

_BACKUP_FILENAME_RE = re.compile(r"^nexo-(\d{4}-\d{2}-\d{2}-\d{4})\.db$")


def list_available_backups() -> list[dict]:
    """Enumerate every candidate backup we know how to restore from.

    Returns a list of dicts with: path, kind, timestamp, size_bytes,
    critical_rows, is_usable. Sorted newest-first.
    """
    entries: list[dict] = []
    backup_base = _backup_base()
    if not backup_base.is_dir():
        return entries

    # Hourly backups from nexo-backup.sh
    for entry in backup_base.glob(HOURLY_BACKUP_GLOB):
        if not entry.is_file():
            continue
        entries.append(_describe_backup(entry, kind="hourly"))

    # Weekly backups
    weekly_dir = backup_base / "weekly"
    if weekly_dir.is_dir():
        for entry in weekly_dir.glob("weekly-*.db"):
            if entry.is_file():
                entries.append(_describe_backup(entry, kind="weekly"))

    # pre-update / pre-autoupdate / pre-recover / pre-heal snapshot dirs
    for subdir in backup_base.iterdir():
        if not subdir.is_dir():
            continue
        name = subdir.name
        if not any(name.startswith(p) for p in ("pre-update-", "pre-autoupdate-", "pre-recover-", "pre-heal-", "pre-migrate-")):
            continue
        nested = subdir / "nexo.db"
        if nested.is_file():
            entries.append(_describe_backup(nested, kind=name.split("-", 1)[0] + "-snapshot"))

    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries


def _describe_backup(path: Path, kind: str) -> dict:
    try:
        stat = path.stat()
    except OSError:
        stat = None
    size = stat.st_size if stat else 0
    mtime = stat.st_mtime if stat else 0.0
    counts: dict[str, int | None] = {}
    critical_rows = 0
    if size > EMPTY_DB_SIZE_BYTES:
        counts = db_row_counts(path, CRITICAL_TABLES)
        critical_rows = sum(v for v in counts.values() if isinstance(v, int))
    return {
        "path": str(path),
        "kind": kind,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime else "",
        "mtime": mtime,
        "size_bytes": size,
        "critical_rows": critical_rows,
        "row_counts": {k: v for k, v in counts.items() if v is not None},
        "is_usable": size > EMPTY_DB_SIZE_BYTES and critical_rows >= MIN_REFERENCE_ROWS,
    }


def _pick_source(entries: list[dict], explicit: str | None) -> tuple[Path | None, str | None]:
    """Return (chosen_path, error)."""
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.exists():
            return None, f"explicit backup not found: {candidate}"
        if candidate.is_dir():
            nested = candidate / "nexo.db"
            if not nested.is_file():
                return None, f"no nexo.db inside directory: {candidate}"
            return nested, None
        return candidate, None

    if not entries:
        return None, "no backups found under NEXO_HOME/backups/"
    for entry in entries:
        if entry["is_usable"]:
            return Path(entry["path"]), None
    return None, "no usable backup with critical rows found"


# ── Recovery flow ───────────────────────────────────────────────────────

def recover(
    source: str | None = None,
    *,
    force: bool = False,
    skip_kill: bool = False,
    dry_run: bool = False,
    target: str | Path | None = None,
) -> dict:
    """Restore nexo.db from a backup. Designed to be safe to call from both
    the MCP tool and the CLI.

    Args:
        source: Optional explicit backup path (file or snapshot dir).
        force: When False and the current DB does NOT look wiped, refuse to
            overwrite unless explicitly forced. Protects against accidental
            rollbacks of a healthy DB.
        skip_kill: Skip the MCP-server-kill step (useful in tests).
        dry_run: Report what would happen without touching disk.
        target: Override the target DB path (defaults to ~/.nexo/data/nexo.db).
    """
    target_path = Path(target).expanduser() if target else _primary_db()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    result: dict = {
        "ok": False,
        "dry_run": dry_run,
        "target": str(target_path),
        "source": None,
        "steps": [],
        "warnings": [],
        "errors": [],
        "current_looks_wiped": db_looks_wiped(target_path),
        "current_row_counts": {k: v for k, v in db_row_counts(target_path).items() if v is not None},
    }

    # Step 1: pick source
    entries = list_available_backups()
    result["available_backups"] = [
        {
            "path": entry["path"],
            "kind": entry["kind"],
            "timestamp": entry["timestamp"],
            "size_bytes": entry["size_bytes"],
            "critical_rows": entry["critical_rows"],
            "is_usable": entry["is_usable"],
        }
        for entry in entries[:10]
    ]

    chosen, err = _pick_source(entries, source)
    if err or chosen is None:
        result["errors"].append(err or "no backup chosen")
        return result
    result["source"] = str(chosen)
    result["steps"].append(f"chose source: {chosen}")

    source_counts = db_row_counts(chosen)
    result["source_row_counts"] = {k: v for k, v in source_counts.items() if v is not None}
    source_total = sum(v for v in source_counts.values() if isinstance(v, int))
    if source_total < MIN_REFERENCE_ROWS:
        result["errors"].append(
            f"chosen backup has only {source_total} rows in critical tables "
            f"(minimum {MIN_REFERENCE_ROWS}). Refusing to restore."
        )
        return result

    # Step 2: safety gate for healthy DBs
    if not force and not result["current_looks_wiped"]:
        current_total = sum(
            v for v in result["current_row_counts"].values() if isinstance(v, int)
        )
        if current_total >= MIN_REFERENCE_ROWS:
            result["errors"].append(
                f"current nexo.db has {current_total} rows in critical tables "
                "and does not look wiped. Re-run with force=True to override."
            )
            return result

    if dry_run:
        result["ok"] = True
        result["steps"].append("dry-run: stopping before any write")
        return result

    stopped_launchagents: list[str] = []

    # Step 3: quiesce live DB writers
    if not skip_kill:
        quiesce_report = quiesce_nexo_db_writers(dry_run=False)
        result["quiesce"] = quiesce_report
        stopped_launchagents = list((quiesce_report.get("launchagents") or {}).get("stopped") or [])
        result["steps"].append(
            "quiesce_db_writers: "
            f"terminated={quiesce_report.get('terminated', 0)} "
            f"launchagents={len(stopped_launchagents)}"
        )
        if quiesce_report.get("errors"):
            result["warnings"].extend(quiesce_report["errors"])

    # Step 4: snapshot current state to pre-recover/
    pre_recover_dir = _backup_base() / f"pre-recover-{time.strftime('%Y-%m-%d-%H%M%S')}"
    if target_path.is_file():
        pre_recover_dir.mkdir(parents=True, exist_ok=True)
        # Copy the main DB plus any sidecar files (-wal, -shm) with shutil so
        # we do NOT lose in-flight WAL content before the restore.
        import shutil as _shutil
        for suffix in ("", "-wal", "-shm"):
            sidecar = target_path.parent / f"{target_path.name}{suffix}"
            if sidecar.exists():
                try:
                    _shutil.copy2(str(sidecar), str(pre_recover_dir / sidecar.name))
                except Exception as e:
                    result["warnings"].append(f"pre-recover snapshot warning ({sidecar.name}): {e}")
        result["pre_recover_dir"] = str(pre_recover_dir)
        result["steps"].append(f"snapshot current state to {pre_recover_dir}")

    # Step 5: copy backup into place via sqlite3.backup, then validate
    # Remove stale WAL/SHM before restore so the new DB starts clean.
    for suffix in ("-wal", "-shm"):
        sidecar = target_path.parent / f"{target_path.name}{suffix}"
        if sidecar.exists():
            try:
                sidecar.unlink()
            except Exception as e:
                result["warnings"].append(f"could not remove {sidecar.name}: {e}")

    ok, copy_err = safe_sqlite_backup(chosen, target_path)
    if not ok:
        result["errors"].append(f"restore copy failed: {copy_err}")
        if stopped_launchagents:
            result["resume"] = resume_nexo_launchagents(stopped_launchagents)
        return result
    result["steps"].append(f"restored {chosen.name} -> {target_path}")

    valid, valid_err = validate_backup_matches_source(chosen, target_path)
    if not valid:
        result["errors"].append(f"post-restore validation failed: {valid_err}")
        if stopped_launchagents:
            result["resume"] = resume_nexo_launchagents(stopped_launchagents)
        return result
    result["steps"].append("validated post-restore row counts")

    final_counts = db_row_counts(target_path)
    result["final_row_counts"] = {k: v for k, v in final_counts.items() if v is not None}
    if stopped_launchagents:
        result["resume"] = resume_nexo_launchagents(stopped_launchagents)
        result["steps"].append(f"resumed {len((result['resume'] or {}).get('started') or [])} launchagent(s)")
    result["ok"] = True
    return result


# ── MCP tool adapter ────────────────────────────────────────────────────

def nexo_recover(
    source: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """MCP tool entry point. Returns a JSON-serialised report."""
    report = recover(
        source=source or None,
        force=force,
        dry_run=dry_run,
    )
    return json.dumps(report, indent=2, ensure_ascii=False, default=str)


TOOLS = [
    (
        nexo_recover,
        "nexo_recover",
        "Restore ~/.nexo/data/nexo.db from the newest hourly backup (or an "
        "explicit source path). Kills live MCP servers, snapshots the current "
        "state to backups/pre-recover-*, and validates post-restore row "
        "counts. Refuses to overwrite a healthy DB unless force=True.",
    ),
]


# ── CLI entrypoint (invoked from src/cli.py) ────────────────────────────

def cli_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="nexo recover",
        description="Restore ~/.nexo/data/nexo.db from the backup stream.",
    )
    parser.add_argument(
        "--from", dest="source", default=None,
        help="Explicit backup path (file or snapshot directory). Defaults to "
             "the newest usable hourly backup.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available backups and exit (no write).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report the plan but do not touch the DB.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the current DB even if it does not look wiped.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human text.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args(argv)

    if args.list:
        entries = list_available_backups()
        if args.json:
            print(json.dumps(entries[:20], indent=2, default=str))
        else:
            if not entries:
                print("No backups found.")
                return 0
            print(f"{'KIND':<18} {'TIMESTAMP':<20} {'SIZE':>10} {'ROWS':>8}  PATH")
            for e in entries[:20]:
                size_mb = e["size_bytes"] / (1024 * 1024)
                usable = "*" if e["is_usable"] else " "
                print(f"{usable}{e['kind']:<17} {e['timestamp']:<20} {size_mb:>9.2f}M {e['critical_rows']:>8}  {e['path']}")
            print("\n* = passes minimum-row floor and is safe to restore from.")
        return 0

    if not args.yes and not args.dry_run and sys.stdin.isatty():
        print("This will overwrite ~/.nexo/data/nexo.db after killing any live MCP servers.")
        print("A snapshot of the current state will be saved to backups/pre-recover-*.")
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    report = recover(
        source=args.source,
        force=args.force,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 1

    print(f"Target: {report['target']}")
    if report.get("source"):
        print(f"Source: {report['source']}")
    print(f"Current looks wiped: {report['current_looks_wiped']}")
    if report.get("pre_recover_dir"):
        print(f"Pre-recover snapshot: {report['pre_recover_dir']}")
    for step in report["steps"]:
        print(f"  - {step}")
    for warn in report["warnings"]:
        print(f"  WARN: {warn}")
    for err in report["errors"]:
        print(f"  ERROR: {err}")
    if report["ok"]:
        final = report.get("final_row_counts", {})
        if final:
            rows = ", ".join(f"{k}={v}" for k, v in sorted(final.items()))
            print(f"Restore OK. Final row counts: {rows}")
        else:
            print("Restore OK.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(cli_main())
