"""Deep Sleep runtime retention.

Keeps Deep Sleep operational artifacts bounded without touching the live memory
databases or the local-context index. The policy is intentionally conservative:
old context dumps are only deleted after their run produced a synthesis or agent
start packet, so failed or incomplete nights remain debuggable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Iterable

DEFAULT_KEEP_DB_BACKUPS = int(os.environ.get("NEXO_DEEP_SLEEP_KEEP_DB_BACKUPS", "3") or "3")
DEFAULT_KEEP_CONTEXTS = int(os.environ.get("NEXO_DEEP_SLEEP_KEEP_CONTEXTS", "7") or "7")
DEFAULT_MAX_LOG_BYTES = int(os.environ.get("NEXO_DEEP_SLEEP_MAX_LOG_BYTES", str(1024 * 1024)) or str(1024 * 1024))
DEFAULT_RETAINED_LOG_BYTES = int(
    os.environ.get("NEXO_DEEP_SLEEP_RETAINED_LOG_BYTES", str(768 * 1024)) or str(768 * 1024)
)
DEBUG_TTL_SECONDS = int(os.environ.get("NEXO_DEEP_SLEEP_DEBUG_TTL_SECONDS", str(7 * 86400)) or str(7 * 86400))

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[-T]?(\d{6}))?")
_DATE_CONTEXT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}(?:[-T]?\d{6})?)-context\.txt$")


def _default_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()


def _dedupe_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen or not path.exists():
            continue
        seen.add(key)
        result.append(path)
    return result


def _deep_sleep_dirs(nexo_home: Path) -> list[Path]:
    return _dedupe_existing(
        [
            nexo_home / "runtime" / "operations" / "deep-sleep",
            nexo_home / "operations" / "deep-sleep",
        ]
    )


def _log_dirs(nexo_home: Path) -> list[Path]:
    return _dedupe_existing(
        [
            nexo_home / "runtime" / "logs",
            nexo_home / "logs",
        ]
    )


def _artifact_sort_key(path: Path) -> tuple[float, str]:
    match = _DATE_PREFIX_RE.match(path.name)
    if match:
        date_part, time_part = match.groups()
        compact = date_part.replace("-", "") + (time_part or "000000")
        try:
            return (float(compact), path.name)
        except ValueError:
            pass
    try:
        return (path.stat().st_mtime, path.name)
    except OSError:
        return (0.0, path.name)


def _analyzed_marker_exists(deep_sleep_dir: Path, run_id: str) -> bool:
    markers = [
        deep_sleep_dir / f"{run_id}-agent-start-packet.json",
        deep_sleep_dir / f"{run_id}-synthesis.json",
        deep_sleep_dir / f"{run_id}-applied.json",
        deep_sleep_dir / run_id / "synthesis.json",
    ]
    return any(path.exists() and path.stat().st_size > 0 for path in markers)


def _path_size(path: Path) -> int:
    try:
        if path.is_dir():
            total = 0
            for child in path.rglob("*"):
                try:
                    if child.is_file() or child.is_symlink():
                        total += child.stat().st_size
                except OSError:
                    continue
            return total
        return path.stat().st_size
    except OSError:
        return 0


def _delete_path(path: Path, *, apply: bool) -> tuple[bool, int]:
    size = _path_size(path)
    if not apply:
        return True, size
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True, size
    except FileNotFoundError:
        return False, 0
    except OSError:
        return False, 0


def _sidecars(path: Path) -> list[Path]:
    candidates = [Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    return [candidate for candidate in candidates if candidate.exists()]


def _record_delete(report: dict, path: Path, *, reason: str, apply: bool) -> None:
    ok, size = _delete_path(path, apply=apply)
    if not ok:
        report["warnings"].append(f"delete-failed:{path}")
        return
    report["deleted_count"] += 1
    report["deleted_bytes"] += size
    report["deleted"].append({"path": str(path), "bytes": size, "reason": reason})


def _prune_db_backups(deep_sleep_dir: Path, report: dict, *, keep: int, apply: bool) -> None:
    for family in ("*-backup-nexo.db", "*-backup-cognitive.db"):
        backups = sorted(deep_sleep_dir.glob(family), key=_artifact_sort_key, reverse=True)
        kept = backups[:keep]
        report["kept"].append({"kind": family, "count": len(kept), "root": str(deep_sleep_dir)})
        for backup in backups[keep:]:
            _record_delete(report, backup, reason=f"old-db-backup:{family}", apply=apply)
            for sidecar in _sidecars(backup):
                _record_delete(report, sidecar, reason=f"old-db-backup-sidecar:{family}", apply=apply)


def _prune_contexts(deep_sleep_dir: Path, report: dict, *, keep: int, apply: bool) -> None:
    contexts: list[tuple[str, Path]] = []
    for path in deep_sleep_dir.glob("*-context.txt"):
        match = _DATE_CONTEXT_RE.match(path.name)
        if not match:
            continue
        contexts.append((match.group(1), path))

    contexts.sort(key=lambda item: _artifact_sort_key(item[1]), reverse=True)
    keep_run_ids = {run_id for run_id, _ in contexts[:keep]}
    report["kept"].append({"kind": "context.txt", "count": min(len(contexts), keep), "root": str(deep_sleep_dir)})

    for run_id, context_file in contexts[keep:]:
        if run_id in keep_run_ids:
            continue
        if not _analyzed_marker_exists(deep_sleep_dir, run_id):
            report["kept"].append({"kind": "unanalyzed-context", "path": str(context_file)})
            continue
        _record_delete(report, context_file, reason="old-analyzed-context", apply=apply)
        date_dir = deep_sleep_dir / run_id
        if date_dir.is_dir():
            _record_delete(report, date_dir, reason="old-analyzed-context-dir", apply=apply)


def _prune_debug_scratch(deep_sleep_dir: Path, report: dict, *, now: float, apply: bool) -> None:
    for pattern in ("debug-extract-*.txt", "debug-synthesize-*.txt"):
        for path in deep_sleep_dir.glob(pattern):
            try:
                if now - path.stat().st_mtime <= DEBUG_TTL_SECONDS:
                    continue
            except OSError:
                continue
            _record_delete(report, path, reason="old-debug-scratch", apply=apply)


def _rotate_log(path: Path, report: dict, *, max_bytes: int, retained_bytes: int, apply: bool) -> None:
    try:
        original_size = path.stat().st_size
    except OSError:
        return
    if original_size <= max_bytes:
        return
    retained_bytes = max(1, min(retained_bytes, max_bytes))
    if not apply:
        report["logs_rotated"] += 1
        report["log_bytes_trimmed"] += max(0, original_size - retained_bytes)
        report["rotated_logs"].append({"path": str(path), "original_bytes": original_size, "dry_run": True})
        return

    try:
        with path.open("rb") as fh:
            if original_size > retained_bytes:
                fh.seek(-retained_bytes, os.SEEK_END)
            tail = fh.read()
        newline = tail.find(b"\n")
        if newline > 0:
            tail = tail[newline + 1 :]
        header = (
            f"[rotated by NEXO Deep Sleep retention; original_bytes={original_size}; "
            f"retained_bytes={len(tail)}]\n"
        ).encode("utf-8")
        path.write_bytes(header + tail)
        new_size = path.stat().st_size
    except OSError as exc:
        report["warnings"].append(f"log-rotate-failed:{path}:{exc.__class__.__name__}")
        return

    report["logs_rotated"] += 1
    report["log_bytes_trimmed"] += max(0, original_size - new_size)
    report["rotated_logs"].append({"path": str(path), "original_bytes": original_size, "new_bytes": new_size})


def _rotate_logs(nexo_home: Path, report: dict, *, max_bytes: int, retained_bytes: int, apply: bool) -> None:
    names = {
        "deep-sleep.log",
        "deep-sleep-stdout.log",
        "deep-sleep-stderr.log",
        "sleep-stdout.log",
        "sleep-stderr.log",
        "prevent-sleep-stdout.log",
        "prevent-sleep-stderr.log",
    }
    for log_dir in _log_dirs(nexo_home):
        for name in names:
            path = log_dir / name
            if path.is_file():
                _rotate_log(path, report, max_bytes=max_bytes, retained_bytes=retained_bytes, apply=apply)


def prune_deep_sleep_runtime(
    *,
    nexo_home: str | Path | None = None,
    apply: bool = False,
    keep_db_backups: int = DEFAULT_KEEP_DB_BACKUPS,
    keep_contexts: int = DEFAULT_KEEP_CONTEXTS,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
    retained_log_bytes: int = DEFAULT_RETAINED_LOG_BYTES,
) -> dict:
    """Apply or plan Deep Sleep retention for a runtime home."""
    home = Path(nexo_home).expanduser() if nexo_home is not None else _default_home()
    keep_db_backups = max(1, int(keep_db_backups))
    keep_contexts = max(1, int(keep_contexts))
    report: dict = {
        "ok": True,
        "apply": bool(apply),
        "nexo_home": str(home),
        "roots": [],
        "deleted_count": 0,
        "deleted_bytes": 0,
        "deleted": [],
        "kept": [],
        "logs_rotated": 0,
        "log_bytes_trimmed": 0,
        "rotated_logs": [],
        "warnings": [],
    }

    now = time.time()
    for deep_sleep_dir in _deep_sleep_dirs(home):
        report["roots"].append(str(deep_sleep_dir))
        _prune_db_backups(deep_sleep_dir, report, keep=keep_db_backups, apply=apply)
        _prune_contexts(deep_sleep_dir, report, keep=keep_contexts, apply=apply)
        _prune_debug_scratch(deep_sleep_dir, report, now=now, apply=apply)
    _rotate_logs(home, report, max_bytes=max_log_bytes, retained_bytes=retained_log_bytes, apply=apply)
    return report


def _print_human(report: dict) -> None:
    mode = "apply" if report.get("apply") else "dry-run"
    print(f"NEXO Deep Sleep retention ({mode})")
    print(f"  roots: {len(report.get('roots') or [])}")
    print(f"  deleted: {report.get('deleted_count', 0)}")
    print(f"  freed/planned: {report.get('deleted_bytes', 0)} bytes")
    print(f"  logs_rotated: {report.get('logs_rotated', 0)}")
    print(f"  log_bytes_trimmed: {report.get('log_bytes_trimmed', 0)}")
    if report.get("warnings"):
        print("  warnings:")
        for warning in report["warnings"]:
            print(f"    - {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXO Deep Sleep retention")
    parser.add_argument("--nexo-home", default=None)
    parser.add_argument("--apply", action="store_true", help="delete/rotate instead of dry-run")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress human output")
    parser.add_argument("--keep-db-backups", type=int, default=DEFAULT_KEEP_DB_BACKUPS)
    parser.add_argument("--keep-contexts", type=int, default=DEFAULT_KEEP_CONTEXTS)
    parser.add_argument("--max-log-bytes", type=int, default=DEFAULT_MAX_LOG_BYTES)
    parser.add_argument("--retained-log-bytes", type=int, default=DEFAULT_RETAINED_LOG_BYTES)
    args = parser.parse_args(argv)

    report = prune_deep_sleep_runtime(
        nexo_home=args.nexo_home,
        apply=args.apply,
        keep_db_backups=args.keep_db_backups,
        keep_contexts=args.keep_contexts,
        max_log_bytes=args.max_log_bytes,
        retained_log_bytes=args.retained_log_bytes,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif not args.quiet:
        _print_human(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
