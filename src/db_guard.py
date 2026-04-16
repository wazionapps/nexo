"""NEXO DB Guard — data-loss detection, validated backups, and self-heal primitives.

This module exists because v5.5.4 surfaced a data-loss incident where
``~/.nexo/data/nexo.db`` was reset to a 4 KB empty-schema file between two
observed states (hourly backup 38 MB → pre-update backup 4 KB). The existing
``plugins/update.py`` copied the already-empty DB into the ``pre-update-*``
directory and reported a successful backup, masking the problem.

Design principles:
- Pure stdlib (sqlite3 + pathlib). No NEXO imports, keeps the module import-safe
  from installer, auto-update, and CLI paths even when the runtime is broken.
- Single source of truth for "what counts as a critical wipe".
- Every operation that writes to a DB is wrapped in a validation pass so a
  silent failure leaves an explicit trail instead of a 4 KB placeholder.

Public surface (stable for use by plugins/update.py, plugins/recover.py,
auto_update.py):

    CRITICAL_TABLES
    WIPE_THRESHOLD_PCT
    MIN_REFERENCE_ROWS

    db_row_counts(path, tables) -> dict[str, int | None]
    db_looks_wiped(path, tables, min_reference_rows) -> bool
    find_latest_hourly_backup(backups_dir, max_age_seconds) -> Path | None
    diff_row_counts(current, reference, tables) -> WipeReport
    safe_sqlite_backup(source, dest) -> tuple[bool, str | None]
    validate_backup_matches_source(source, dest, tables) -> tuple[bool, str | None]
    kill_nexo_mcp_servers(dry_run) -> dict
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


# ── Constants ───────────────────────────────────────────────────────────

# Tables whose row counts we treat as canonical evidence of "DB has real data".
# Kept narrow on purpose: a fresh install has zero rows in some of these
# (e.g. reminders), so the wipe detector requires a reference backup with
# meaningful counts before it fires. See MIN_REFERENCE_ROWS below.
CRITICAL_TABLES: tuple[str, ...] = (
    "protocol_tasks",
    "followups",
    "reminders",
    "learnings",
    "session_diary",
    "guard_checks",
    "protocol_debt",
    "cron_runs",
    "change_log",
    "decisions",
)

# A reference backup must contain at least this many rows (summed across
# CRITICAL_TABLES) before we will treat it as "proof the user has real data".
# Otherwise we cannot distinguish a fresh install from a wipe.
MIN_REFERENCE_ROWS = 50

# If the current DB has lost >= this percentage of rows across CRITICAL_TABLES
# compared to the reference, we call it a wipe. Set conservatively to avoid
# tripping on legitimate churn like reminder cleanup.
WIPE_THRESHOLD_PCT = 80

# Minimum file size (bytes) a non-empty SQLite DB should clearly exceed.
# A fresh schema-only nexo.db is 4096 B. Real data crosses this in minutes.
EMPTY_DB_SIZE_BYTES = 32 * 1024

# Filename prefix produced by ``src/scripts/nexo-backup.sh``.
HOURLY_BACKUP_GLOB = "nexo-*.db"

# Hourly backups older than this (seconds) are considered too stale to use
# as an automatic self-heal source. 48h matches nexo-backup.sh retention.
HOURLY_BACKUP_MAX_AGE = 48 * 3600


# ── Types ───────────────────────────────────────────────────────────────

@dataclass
class TableDiff:
    table: str
    source: int | None
    reference: int | None
    lost_pct: float  # 0..100, meaningful only when reference > 0

    def is_regression(self, threshold_pct: float = WIPE_THRESHOLD_PCT) -> bool:
        if self.reference is None or self.reference == 0:
            return False
        if self.source is None:
            return True
        return self.lost_pct >= threshold_pct


@dataclass
class WipeReport:
    source_counts: dict[str, int | None] = field(default_factory=dict)
    reference_counts: dict[str, int | None] = field(default_factory=dict)
    table_diffs: list[TableDiff] = field(default_factory=list)
    total_source_rows: int = 0
    total_reference_rows: int = 0

    @property
    def overall_lost_pct(self) -> float:
        if self.total_reference_rows <= 0:
            return 0.0
        lost = max(self.total_reference_rows - self.total_source_rows, 0)
        return (lost / self.total_reference_rows) * 100.0

    def is_wipe(
        self,
        threshold_pct: float = WIPE_THRESHOLD_PCT,
        min_reference_rows: int = MIN_REFERENCE_ROWS,
    ) -> bool:
        """Return True only when reference looks real AND we lost >= threshold."""
        if self.total_reference_rows < min_reference_rows:
            return False
        if self.overall_lost_pct >= threshold_pct:
            return True
        # Also flag when 2+ individual critical tables each dropped >= threshold
        regressions = sum(1 for d in self.table_diffs if d.is_regression(threshold_pct))
        return regressions >= 2

    def summary_lines(self) -> list[str]:
        lines = [
            f"  source rows (critical tables): {self.total_source_rows}",
            f"  reference rows (critical tables): {self.total_reference_rows}",
            f"  overall loss: {self.overall_lost_pct:.1f}%",
        ]
        regressions = [d for d in self.table_diffs if d.is_regression()]
        if regressions:
            lines.append("  regressions:")
            for d in regressions:
                src = "missing" if d.source is None else str(d.source)
                lines.append(f"    - {d.table}: {d.reference} -> {src} ({d.lost_pct:.1f}% lost)")
        return lines


# ── Row count primitives ────────────────────────────────────────────────

def _table_count(conn: sqlite3.Connection, table: str) -> int | None:
    """Return COUNT(*) for ``table`` or None if the table is missing."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    result = cur.fetchone()
    return int(result[0]) if result is not None else 0


def db_row_counts(path: str | Path, tables: tuple[str, ...] = CRITICAL_TABLES) -> dict[str, int | None]:
    """Return {table: count} for a SQLite DB. Missing DB / missing tables map to None."""
    p = Path(path)
    counts: dict[str, int | None] = {t: None for t in tables}
    if not p.is_file():
        return counts
    try:
        conn = sqlite3.connect(str(p), timeout=5)
    except Exception:
        return counts
    try:
        for table in tables:
            try:
                counts[table] = _table_count(conn, table)
            except Exception:
                counts[table] = None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return counts


def db_looks_wiped(
    path: str | Path,
    tables: tuple[str, ...] = CRITICAL_TABLES,
    min_reference_rows: int = MIN_REFERENCE_ROWS,
) -> bool:
    """Heuristic: the file exists AND either all critical tables exist with 0 rows,
    OR the file is suspiciously close to the empty-schema size (4 KB).

    Returns False when the DB is missing entirely — that is a separate condition
    handled by the caller (nothing to protect vs. something to restore).
    """
    p = Path(path)
    if not p.is_file():
        return False
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size <= EMPTY_DB_SIZE_BYTES:
        # Small but not necessarily wiped — confirm via row counts.
        counts = db_row_counts(p, tables)
        return _all_tables_empty_or_missing(counts)
    counts = db_row_counts(p, tables)
    return _all_tables_empty_or_missing(counts)


def _all_tables_empty_or_missing(counts: dict[str, int | None]) -> bool:
    """True when every critical table is either missing or 0 rows."""
    if not counts:
        return False
    for val in counts.values():
        if val is not None and val > 0:
            return False
    return True


# ── Reference backup discovery ──────────────────────────────────────────

def find_latest_hourly_backup(
    backups_dir: str | Path,
    max_age_seconds: int = HOURLY_BACKUP_MAX_AGE,
    glob: str = HOURLY_BACKUP_GLOB,
    min_critical_rows: int = 1,
) -> Path | None:
    """Return the newest hourly backup that contains at least ``min_critical_rows``
    across CRITICAL_TABLES and is not older than ``max_age_seconds``.

    Row count is used rather than file size because a busy install accumulates
    thousands of small rows in minutes, so size alone is a poor heuristic and
    fails on test fixtures. The whole point of the guard is that file size
    lies when the source has been silently wiped.
    """
    base = Path(backups_dir)
    if not base.is_dir():
        return None
    now = time.time()
    # Step 1: cheap stat-only pass (no sqlite open) — produces sorted newest-first.
    stat_candidates: list[tuple[float, Path]] = []
    for entry in base.glob(glob):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if now - stat.st_mtime > max_age_seconds:
            continue
        if stat.st_size <= EMPTY_DB_SIZE_BYTES:
            continue  # Clearly empty schema file.
        stat_candidates.append((stat.st_mtime, entry))
    if not stat_candidates:
        return None
    stat_candidates.sort(key=lambda pair: pair[0], reverse=True)
    # Step 2: open backups newest-first and return the first one that passes
    # the row-count floor. A production NEXO_HOME can accumulate 40+ hourly
    # backups, so opening every file would add seconds to the CLI startup.
    for _, candidate in stat_candidates:
        counts = db_row_counts(candidate)
        total = sum(v for v in counts.values() if isinstance(v, int))
        if total >= min_critical_rows:
            return candidate
    return None


# ── Diff & wipe detection ───────────────────────────────────────────────

def diff_row_counts(
    current: str | Path,
    reference: str | Path,
    tables: tuple[str, ...] = CRITICAL_TABLES,
) -> WipeReport:
    """Compare row counts between two SQLite DBs and return a WipeReport."""
    source_counts = db_row_counts(current, tables)
    reference_counts = db_row_counts(reference, tables)

    report = WipeReport(
        source_counts=source_counts,
        reference_counts=reference_counts,
    )
    for table in tables:
        src = source_counts.get(table)
        ref = reference_counts.get(table)
        if src is not None:
            report.total_source_rows += src
        if ref is not None:
            report.total_reference_rows += ref
        if ref is None or ref == 0:
            lost_pct = 0.0
        elif src is None:
            lost_pct = 100.0
        else:
            lost_pct = max(0.0, (ref - src) / ref * 100.0)
        report.table_diffs.append(TableDiff(
            table=table,
            source=src,
            reference=ref,
            lost_pct=lost_pct,
        ))
    return report


# ── Validated SQLite backup ────────────────────────────────────────────

def safe_sqlite_backup(source: str | Path, dest: str | Path) -> tuple[bool, str | None]:
    """Copy ``source`` to ``dest`` via sqlite3's online backup API.

    Returns (True, None) on success, (False, reason) on failure. Creates the
    destination directory if missing. Does NOT validate that the copy contains
    rows — that is the caller's job via validate_backup_matches_source().
    """
    src = Path(source)
    dst = Path(dest)
    if not src.is_file():
        return False, f"source missing: {src}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"cannot create dest dir: {e}"
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src), timeout=30)
        dst_conn = sqlite3.connect(str(dst), timeout=30)
        src_conn.backup(dst_conn)
    except Exception as e:
        return False, f"sqlite3.backup failed: {e}"
    finally:
        for conn in (dst_conn, src_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return True, None


def validate_backup_matches_source(
    source: str | Path,
    dest: str | Path,
    tables: tuple[str, ...] = CRITICAL_TABLES,
) -> tuple[bool, str | None]:
    """After a backup, verify that every critical table in the copy has at
    least as many rows as the source — i.e. we did not lose data in transit.

    Tables missing from both sides are ignored. Tables present in source but
    missing in dest return an explicit error.
    """
    src = Path(source)
    dst = Path(dest)
    if not dst.is_file():
        return False, f"backup missing at {dst}"
    source_counts = db_row_counts(src, tables)
    dest_counts = db_row_counts(dst, tables)
    discrepancies: list[str] = []
    for table in tables:
        s = source_counts.get(table)
        d = dest_counts.get(table)
        if s is None and d is None:
            continue
        if s is not None and d is None:
            discrepancies.append(f"{table}: source={s} backup=missing")
            continue
        if s is not None and d is not None and d < s:
            discrepancies.append(f"{table}: source={s} backup={d}")
    if discrepancies:
        return False, "; ".join(discrepancies)
    return True, None


# ── MCP server discovery / kill ─────────────────────────────────────────

def kill_nexo_mcp_servers(dry_run: bool = False) -> dict:
    """Best-effort: find and terminate any running NEXO MCP server processes.

    Used before `nexo recover` overwrites ~/.nexo/data/nexo.db so a live server
    does not keep a stale connection that immediately re-writes the restored
    file. Never raises — callers treat failures as "maybe still alive".

    Returns: {scanned, terminated, errors, dry_run, pids}
    """
    result: dict = {
        "scanned": 0,
        "terminated": 0,
        "errors": [],
        "dry_run": dry_run,
        "pids": [],
    }
    if os.name != "posix":
        result["errors"].append("unsupported platform")
        return result
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as e:
        result["errors"].append(f"ps failed: {e}")
        return result
    if proc.returncode != 0:
        result["errors"].append(f"ps exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        return result

    my_pid = os.getpid()
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if not head.isdigit():
            continue
        pid = int(head)
        if pid == my_pid:
            continue
        cmd = rest.strip()
        if not _looks_like_nexo_mcp(cmd):
            continue
        result["scanned"] += 1
        result["pids"].append({"pid": pid, "command": cmd[:180]})
        if dry_run:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            result["terminated"] += 1
        except ProcessLookupError:
            pass
        except Exception as e:
            result["errors"].append(f"kill {pid} failed: {e}")
    return result


def _looks_like_nexo_mcp(cmd: str) -> bool:
    """Heuristic: is this command line a NEXO MCP server worth terminating?"""
    if not cmd:
        return False
    lowered = cmd.lower()
    # server.py is the MCP entrypoint; fastmcp is the framework marker; avoid
    # matching the generic claude binary which may be running other servers.
    if "server.py" in lowered and "nexo" in lowered:
        return True
    if "fastmcp" in lowered and "nexo" in lowered:
        return True
    if "nexo_sdk" in lowered or "nexo-mcp" in lowered:
        return True
    return False
