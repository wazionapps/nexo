#!/usr/bin/env python3
# nexo: name=prune-runtime-backups
# nexo: description=Rotate technical rollback snapshots under runtime/backups by family. Never touches business (shopify-backups) or hourly_db (nexo-backup.sh) artifacts.
# nexo: category=maintenance
# nexo: runtime=python
# nexo: timeout=300
# nexo: idempotent=true

"""
prune_runtime_backups.py — NEXO backup retention by class.

Separates *technical* rollback snapshots (throwaway, produced by the installer,
updater and backfills) from *operational* snapshots (shopify-backups, hourly
DB dumps, weekly archives) so the former can be rotated without risk to the
latter.

Target: $NEXO_HOME/runtime/backups/ (default ~/.nexo/runtime/backups)

Class taxonomy (prefix-based) and retention policy:

  TECHNICAL (rollback snapshots, produced by installer/updater/backfills):
    Prefixes:
      pre-update-*, pre-autoupdate-*, pre-backfill-owner-*,
      pre-runtime-sync-*, pre-sleep-wrapper-*, pre-obs-clean-*,
      pre-import-user-data-*, pre-backfill-*, pre-heal-*, pre-recover-*,
      code-tree-*, runtime-tree-*,
      app-install-*, app-reinstall-*, desktop-local-install-*,
      packaged-code-f06-conflicts-*, legacy-shim-conflicts-*,
      legacy-personal-brain-db-stubs-*, legacy-root-db-stubs-*,
      codex-live-sync-*, layout-loop-cleanup-*,
      aux-launchagents-restore-*, live-sync-*, manual-*,
      personal-script-legacy-prefix-*, plist-f06fix-*,
      retired-personal-scripts-*, retired-personal-skills-*,
      runtime-core-sync-*, pre-freshinstall-*
    Retention (per prefix family): keep last N_RECENT + 1 per month for
    MONTHLY_WINDOW_DAYS. Older than that and outside the recent window
    are eligible for deletion.

  HOURLY_DB (sqlite dumps, managed by nexo-backup.sh):
    Prefix: nexo-YYYY-MM-DD-HHMM.db in runtime/backups/ root
    These are already rotated by nexo-backup.sh (48h retention). We skip
    them here to avoid double-rotation logic.

  WEEKLY_DB (weekly/ directory):
    Already rotated by nexo-backup.sh (90d retention). Skip.

  BUSINESS (shopify-backups/ and similar protected directories):
    Prefix/name: shopify-backups (directory). Never touched.

Usage:
  prune_runtime_backups.py                 # dry-run summary
  prune_runtime_backups.py --apply         # actually delete
  prune_runtime_backups.py --json          # machine-readable report
  prune_runtime_backups.py --recent 5      # override N_RECENT
  prune_runtime_backups.py --window-days 90
  prune_runtime_backups.py --only pre-backfill-owner  # restrict family

Exit codes:
  0  success (or nothing to prune)
  1  bad arguments or fatal I/O error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Technical prefixes. Order defines precedence when a name matches several.
TECHNICAL_PREFIXES = (
    "pre-update-",
    "pre-autoupdate-",
    "pre-backfill-owner-",
    "pre-backfill-",
    "pre-runtime-sync-",
    "pre-sleep-wrapper-",
    "pre-obs-clean-",
    "pre-import-user-data-",
    "pre-heal-",
    "pre-recover-",
    "pre-freshinstall-",
    "code-tree-",
    "runtime-tree-",
    "app-install-",
    "app-reinstall-",
    "desktop-local-install-",
    "packaged-code-f06-conflicts-",
    "legacy-shim-conflicts-",
    "legacy-cognitive-db-",
    "legacy-personal-brain-db-stubs-",
    "legacy-root-db-stubs-",
    "codex-live-sync-",
    "layout-loop-cleanup-",
    "aux-launchagents-restore-",
    "live-sync-",
    "manual-",
    "personal-script-legacy-prefix-",
    "plist-f06fix-",
    "retired-personal-scripts-",
    "retired-personal-skills-",
    "runtime-core-sync-",
)

# Entries that must never be considered for pruning.
PROTECTED_NAMES = {"shopify-backups", "weekly"}
# Hourly DB dumps at the root of runtime/backups — managed by nexo-backup.sh.
HOURLY_DB_RE = re.compile(r"^nexo-\d{4}-\d{2}-\d{2}-\d{4}\.db$")
LOCAL_CONTEXT_DB_RE = re.compile(r"^local-context-\d{4}-\d{2}-\d{2}-\d{4}(\d{2})?\.db$")
TEMPORARY_RE = re.compile(r"(^|.*[.])tmp([.-].*)?$|.*\.tmp\..*|.*-journal$|.*\.db-(wal|shm)$")
# Big ad-hoc DB files at the root — rare, include for reporting but never auto-prune.
ROOT_DB_RE = re.compile(r"^(pre-obs-clean|pre-sleep-wrapper-apply|pre-.*)-\d{4}-\d{2}-\d{2}-\d{4}\.db$")
DEFAULT_MAX_BYTES = 50 * 1024 * 1024 * 1024
MIN_ADAPTIVE_MAX_BYTES = 10 * 1024 * 1024 * 1024
MAX_ADAPTIVE_MAX_BYTES = 50 * 1024 * 1024 * 1024

# Timestamp patterns embedded in directory names.
TS_PATTERNS = (
    # e.g. 2026-04-20-0427 or 2026-04-20-042733
    re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})?$"),
    # e.g. 20260420-083106
    re.compile(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})?$"),
)


def default_nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def parse_timestamp(name: str) -> datetime | None:
    for pat in TS_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        parts = [int(x) for x in m.groups() if x is not None]
        # year, month, day, hour, minute, [second]
        try:
            if len(parts) == 5:
                y, mo, d, h, mi = parts
                s = 0
            else:
                y, mo, d, h, mi, s = parts
            return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def classify(name: str) -> tuple[str, str] | None:
    """Return (class, family) or None if the entry should be ignored."""
    if name in PROTECTED_NAMES:
        return ("BUSINESS", name)
    if TEMPORARY_RE.match(name):
        return ("TEMPORARY", "temporary")
    if LOCAL_CONTEXT_DB_RE.match(name):
        return ("LOCAL_CONTEXT_DB", "local-context")
    if HOURLY_DB_RE.match(name):
        return ("HOURLY_DB", "nexo-db")
    if ROOT_DB_RE.match(name):
        return ("ROOT_DB", "root-db")
    for pref in TECHNICAL_PREFIXES:
        if name.startswith(pref):
            return ("TECHNICAL", pref.rstrip("-"))
    # Unknown: report but never touch.
    return ("UNKNOWN", "unknown")


def dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
            for fn in files:
                fp = Path(root) / fn
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def parse_size_bytes(value: str | int | None, *, default: int = DEFAULT_MAX_BYTES) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return max(0, value)
    raw = str(value).strip().lower()
    if not raw:
        return default
    multiplier = 1
    if raw[-1:] in {"k", "m", "g", "t"}:
        unit = raw[-1]
        raw = raw[:-1].strip()
        multiplier = {
            "k": 1024,
            "m": 1024 ** 2,
            "g": 1024 ** 3,
            "t": 1024 ** 4,
        }[unit]
    try:
        return max(0, int(float(raw) * multiplier))
    except ValueError:
        return default


def effective_max_bytes(backups_root: Path, raw_max_bytes: int) -> int:
    """Apply the adaptive default cap without blocking emergency lower caps."""
    if raw_max_bytes < MIN_ADAPTIVE_MAX_BYTES:
        return raw_max_bytes
    probe = backups_root if backups_root.exists() else backups_root.parent
    try:
        total = int(shutil.disk_usage(str(probe)).total)
        adaptive = int(total * 0.05)
    except Exception:
        adaptive = DEFAULT_MAX_BYTES
    adaptive = max(MIN_ADAPTIVE_MAX_BYTES, min(MAX_ADAPTIVE_MAX_BYTES, adaptive))
    return min(raw_max_bytes, adaptive)


def gather_entries(backups_root: Path) -> list[dict]:
    items: list[dict] = []
    for entry in backups_root.iterdir():
        name = entry.name
        cls = classify(name)
        if cls is None:
            continue
        klass, family = cls
        ts = parse_timestamp(name)
        if ts is None:
            try:
                ts = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            except OSError:
                ts = None
        try:
            size = dir_size_bytes(entry) if entry.is_dir() else entry.stat().st_size
        except OSError:
            continue
        items.append({
            "name": name,
            "path": str(entry),
            "class": klass,
            "family": family,
            "ts": ts,
            "size": size,
            "is_dir": entry.is_dir(),
        })
    return items


def path_key(path: str | Path) -> str:
    candidate = Path(path)
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate.absolute())


def plan_prunes(
    items: list[dict],
    *,
    n_recent: int,
    window_days: int,
    only: str | None,
    max_bytes: int,
    tmp_ttl_seconds: int,
    local_context_keep: int,
    hourly_keep: int,
) -> tuple[list[dict], list[dict]]:
    """Return (to_delete, to_keep) for product-generated backup artifacts."""
    now = datetime.now(tz=timezone.utc)
    to_delete: list[dict] = []
    to_keep: list[dict] = []
    delete_ids: set[int] = set()
    by_family: dict[str, list[dict]] = {}
    for it in items:
        if it["class"] != "TECHNICAL":
            continue
        if only and it["family"] != only:
            continue
        by_family.setdefault(it["family"], []).append(it)

    for family, group in by_family.items():
        group.sort(key=lambda x: (x["ts"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        # Keep the N_RECENT most recent unconditionally.
        keep_recent = group[:n_recent]
        older = group[n_recent:]
        recent_ts = {id(x) for x in keep_recent}
        # From older, keep one per (year, month) if within window_days. The
        # rest are pruned.
        seen_months: set[tuple[int, int]] = set()
        for it in older:
            ts = it["ts"]
            age_days = (now - ts).days if ts else 10_000
            if age_days <= window_days and ts is not None:
                ym = (ts.year, ts.month)
                if ym not in seen_months:
                    seen_months.add(ym)
                    to_keep.append(it)
                    continue
            if id(it) not in delete_ids:
                to_delete.append(it)
                delete_ids.add(id(it))
        to_keep.extend(keep_recent)

    for it in items:
        if it["class"] != "TEMPORARY":
            continue
        ts = it["ts"]
        age_seconds = (now - ts).total_seconds() if ts else 10_000_000
        if age_seconds >= tmp_ttl_seconds:
            if id(it) not in delete_ids:
                to_delete.append(it)
                delete_ids.add(id(it))
        else:
            to_keep.append(it)

    for klass, keep_count in (("LOCAL_CONTEXT_DB", local_context_keep),):
        group = [it for it in items if it["class"] == klass]
        group.sort(key=lambda x: (x["ts"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        for it in group[:max(0, keep_count)]:
            to_keep.append(it)
        for it in group[max(0, keep_count):]:
            if id(it) not in delete_ids:
                to_delete.append(it)
                delete_ids.add(id(it))

    if max_bytes > 0:
        total_after_planned = sum(i["size"] for i in items if id(i) not in delete_ids)
        if total_after_planned > max_bytes:
            protected_keep_ids: set[int] = set()
            by_budget_family: dict[tuple[str, str], list[dict]] = {}
            for it in items:
                by_budget_family.setdefault((it["class"], it["family"]), []).append(it)
            for (klass, _family), group in by_budget_family.items():
                if klass not in {"TECHNICAL", "LOCAL_CONTEXT_DB", "TEMPORARY"}:
                    continue
                min_keep = 0
                if klass == "LOCAL_CONTEXT_DB":
                    min_keep = max(0, local_context_keep)
                group.sort(key=lambda x: (x["ts"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
                for it in group[:min_keep]:
                    protected_keep_ids.add(id(it))

            budget_candidates = [
                it for it in items
                if id(it) not in delete_ids
                and id(it) not in protected_keep_ids
                and it["class"] in {"TECHNICAL", "LOCAL_CONTEXT_DB", "TEMPORARY"}
            ]
            budget_candidates.sort(
                key=lambda x: (
                    x["ts"] or datetime.min.replace(tzinfo=timezone.utc),
                    -int(x["size"] or 0),
                )
            )
            for it in budget_candidates:
                if total_after_planned <= max_bytes:
                    break
                to_delete.append(it)
                delete_ids.add(id(it))
                total_after_planned -= int(it["size"] or 0)

    keep_ids = {id(i) for i in to_keep}
    for it in items:
        if id(it) not in delete_ids and id(it) not in keep_ids:
            to_keep.append(it)
    return to_delete, to_keep


def restore_point_guard(items: list[dict], to_delete: list[dict]) -> tuple[bool, list[str], dict]:
    """Validate that apply mode never removes protected restore classes."""
    delete_ids = {id(item) for item in to_delete}
    protected_classes = {"BUSINESS", "HOURLY_DB", "ROOT_DB", "UNKNOWN"}
    protected_names = set(PROTECTED_NAMES)
    violations: list[str] = []
    for item in items:
        if id(item) not in delete_ids:
            continue
        if item["class"] in protected_classes or item["name"] in protected_names:
            violations.append(f"{item['name']} ({item['class']})")
    hourly_count = sum(1 for item in items if item["class"] == "HOURLY_DB")
    weekly_present = any(item["name"] == "weekly" for item in items)
    business_count = sum(1 for item in items if item["class"] == "BUSINESS")
    return not violations, violations, {
        "hourly_db_present": hourly_count,
        "weekly_present": weekly_present,
        "business_protected": business_count,
        "protected_delete_violations": violations,
    }


def run(args: argparse.Namespace) -> int:
    backups_root = Path(args.root or (default_nexo_home() / "runtime" / "backups"))
    if not backups_root.is_dir():
        print(f"ERROR: backups root not found: {backups_root}", file=sys.stderr)
        return 1
    items = gather_entries(backups_root)
    tech_items = [i for i in items if i["class"] == "TECHNICAL"]
    biz_items = [i for i in items if i["class"] == "BUSINESS"]
    hourly_items = [i for i in items if i["class"] == "HOURLY_DB"]
    root_db_items = [i for i in items if i["class"] == "ROOT_DB"]
    local_context_items = [i for i in items if i["class"] == "LOCAL_CONTEXT_DB"]
    temporary_items = [i for i in items if i["class"] == "TEMPORARY"]
    unknown_items = [i for i in items if i["class"] == "UNKNOWN"]
    max_bytes = effective_max_bytes(backups_root, parse_size_bytes(args.max_bytes))

    to_delete, to_keep = plan_prunes(
        items,
        n_recent=args.recent,
        window_days=args.window_days,
        only=args.only,
        max_bytes=max_bytes,
        tmp_ttl_seconds=max(0, args.tmp_ttl_minutes) * 60,
        local_context_keep=max(0, args.local_context_keep),
        hourly_keep=max(0, args.hourly_keep),
    )
    protected_paths = {path_key(path) for path in (args.protect or [])}
    protected_ids: set[int] = set()
    if protected_paths:
        protected_items = [item for item in items if path_key(item["path"]) in protected_paths]
        protected_ids = {id(item) for item in protected_items}
        if protected_ids:
            to_delete = [item for item in to_delete if id(item) not in protected_ids]
            keep_ids = {id(item) for item in to_keep}
            to_keep.extend(item for item in protected_items if id(item) not in keep_ids)

    if args.delete_all_technical:
        delete_ids = {id(item) for item in to_delete}
        for item in items:
            if (
                item["class"] in {"TECHNICAL", "TEMPORARY"}
                and id(item) not in delete_ids
                and id(item) not in protected_ids
            ):
                to_delete.append(item)
                delete_ids.add(id(item))
        to_keep = [item for item in items if id(item) not in delete_ids]

    restore_guard_ok, restore_guard_violations, restore_guard = restore_point_guard(items, to_delete)

    total_all = sum(i["size"] for i in items)
    total_del = sum(i["size"] for i in to_delete)

    report = {
        "root": str(backups_root),
        "now_utc": datetime.now(tz=timezone.utc).isoformat(),
        "policy": {
            "n_recent": args.recent,
            "window_days": args.window_days,
            "only": args.only,
            "max_bytes": max_bytes,
            "max_human": human_size(max_bytes),
            "delete_all_technical": args.delete_all_technical,
            "tmp_ttl_minutes": args.tmp_ttl_minutes,
            "local_context_keep": args.local_context_keep,
            "hourly_keep": args.hourly_keep,
            "protected_paths": sorted(protected_paths),
            "restore_point_guard": restore_guard,
        },
        "totals": {
            "all_bytes": total_all,
            "all_human": human_size(total_all),
            "delete_bytes": total_del,
            "delete_human": human_size(total_del),
            "delete_count": len(to_delete),
        },
        "counts_by_class": {
            "technical": len(tech_items),
            "business": len(biz_items),
            "hourly_db": len(hourly_items),
            "local_context_db": len(local_context_items),
            "temporary": len(temporary_items),
            "root_db": len(root_db_items),
            "unknown": len(unknown_items),
        },
        "delete": [
            {"name": i["name"], "family": i["family"], "size": i["size"],
             "ts": i["ts"].isoformat() if i["ts"] else None}
            for i in sorted(to_delete, key=lambda x: x["size"], reverse=True)
        ],
        "keep_sample": [
            {"name": i["name"], "family": i["family"], "size": i["size"],
             "ts": i["ts"].isoformat() if i["ts"] else None}
            for i in sorted(to_keep, key=lambda x: (x["family"], x["ts"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:30]
        ],
        "unknown": [i["name"] for i in unknown_items],
    }

    if not args.json:
        print(f"NEXO backup prune — root: {backups_root}")
        print(f"  total on disk:   {human_size(total_all)}  ({len(items)} entries)")
        print(f"    technical:     {len(tech_items)}")
        print(f"    business:      {len(biz_items)} (protected)")
        print(f"    hourly_db:     {len(hourly_items)} (managed by nexo-backup.sh)")
        print(f"    local_context: {len(local_context_items)}")
        print(f"    temporary:     {len(temporary_items)}")
        print(f"    root_db:       {len(root_db_items)} (never auto-pruned)")
        print(f"    unknown:       {len(unknown_items)}")
        print(f"  policy: keep {args.recent} most-recent + 1 per month within {args.window_days}d; hard-cap {human_size(max_bytes)}")
        if args.only:
            print(f"  restricted to family: {args.only}")
        print()
        print(f"  would free: {human_size(total_del)}  ({len(to_delete)} entries)")
        if to_delete:
            print("\nTOP 20 candidates:")
            for it in sorted(to_delete, key=lambda x: x["size"], reverse=True)[:20]:
                ts = it["ts"].strftime("%Y-%m-%d %H:%M") if it["ts"] else "?"
                print(f"  - {human_size(it['size']):>8}  {ts}  {it['name']}")
        if unknown_items:
            print("\nUNKNOWN entries (never pruned — review manually):")
            for it in unknown_items[:20]:
                print(f"  ? {it['name']}")

    if not args.apply:
        if args.json:
            print(json.dumps(report, indent=2))
            return 0
        if not args.json:
            print("\n(dry-run: pass --apply to delete)")
        return 0

    if not restore_guard_ok:
        print(
            "ERROR: refusing to prune protected restore artifacts: "
            + ", ".join(restore_guard_violations),
            file=sys.stderr,
        )
        return 1

    deleted = 0
    failed = 0
    freed = 0
    for it in to_delete:
        p = Path(it["path"])
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted += 1
            freed += it["size"]
        except OSError as e:
            failed += 1
            print(f"WARN: failed to delete {p}: {e}", file=sys.stderr)
    report["apply"] = {
        "deleted": deleted,
        "freed_bytes": freed,
        "freed_human": human_size(freed),
        "failures": failed,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nDELETED {deleted} entries, freed {human_size(freed)}, failures: {failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="NEXO runtime backups prune (technical rollback tiers).")
    ap.add_argument("--root", help="override runtime/backups path")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--recent", type=int, default=5, help="N most recent per family to always keep (default: 5)")
    ap.add_argument("--window-days", type=int, default=90, help="month-spaced retention window (default: 90)")
    ap.add_argument("--only", help="restrict to one technical family (e.g. 'pre-backfill-owner')")
    ap.add_argument("--max-bytes", default=os.environ.get("NEXO_BACKUP_MAX_BYTES", str(DEFAULT_MAX_BYTES)), help="global product-generated backup hard cap, bytes or K/M/G/T (default: 50G)")
    ap.add_argument("--delete-all-technical", action="store_true", help="emergency mode: delete all technical rollback snapshots; protected business/weekly/hourly DB backups remain untouched")
    ap.add_argument("--tmp-ttl-minutes", type=int, default=int(os.environ.get("NEXO_BACKUP_TMP_TTL_MINUTES", "30")), help="delete orphan temporary backup files older than this (default: 30)")
    ap.add_argument("--local-context-keep", type=int, default=int(os.environ.get("NEXO_LOCAL_CONTEXT_BACKUP_KEEP_LAST", "1")), help="local-context backup files to keep under the global cap (default: 1)")
    ap.add_argument("--hourly-keep", type=int, default=int(os.environ.get("NEXO_BACKUP_KEEP_LAST", "3")), help="hourly nexo DB backups to keep under the global cap (default: 3)")
    ap.add_argument("--protect", action="append", default=[], help="backup path to keep even if it is otherwise prunable; repeat for multiple paths")
    args = ap.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
