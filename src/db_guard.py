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
    LOCAL_CONTEXT_TABLES
    PROTECTED_TABLES
    WIPE_THRESHOLD_PCT
    MIN_REFERENCE_ROWS

    db_row_counts(path, tables) -> dict[str, int | None]
    db_looks_wiped(path, tables, min_reference_rows) -> bool
    find_latest_hourly_backup(backups_dir, max_age_seconds) -> Path | None
    find_best_hourly_backup(backups_dir, max_age_seconds) -> Path | None
    diff_row_counts(current, reference, tables) -> WipeReport
    safe_sqlite_backup(source, dest) -> tuple[bool, str | None]
    validate_backup_matches_source(source, dest, tables) -> tuple[bool, str | None]
    restore_tables_from_backup(source, target, tables) -> dict
    kill_nexo_mcp_servers(dry_run) -> dict
    quiesce_nexo_db_writers(dry_run) -> dict
    resume_nexo_launchagents(labels, dry_run) -> dict
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

# The local memory index is not a disposable cache. A full-disk first pass can
# take days, and losing these tables silently makes NEXO look "healthy" while
# the user's local memory has actually been reset.
LOCAL_CONTEXT_TABLES: tuple[str, ...] = (
    "local_index_roots",
    "local_index_exclusions",
    "local_index_jobs",
    "local_index_checkpoints",
    "local_index_state",
    "local_index_errors",
    "local_index_logs",
    "local_assets",
    "local_asset_versions",
    "local_chunks",
    "local_entities",
    "local_relations",
    "local_embeddings",
    "local_context_queries",
    "local_index_dirs",
)

# Tables protected inside the operational Brain DB. Keep this core-only:
# callers that need local memory checks must request LOCAL_CONTEXT_TABLES or
# RECOVERY_TABLES explicitly so normal Brain wipe detection is not skewed by
# split local-memory databases.
PROTECTED_TABLES: tuple[str, ...] = CRITICAL_TABLES

# Recovery must still inspect legacy backups that carried local memory tables
# inside nexo.db. This wider set is safe for row counts and restore validation,
# but should not replace PROTECTED_TABLES in core wipe-diff callers.
RECOVERY_TABLES: tuple[str, ...] = CRITICAL_TABLES + LOCAL_CONTEXT_TABLES

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

# Long-lived NEXO services that can keep ``nexo.db`` open while recovery tries
# to replace it. Keep this list conservative: only product-owned background
# processes that are safe to stop and restart.
NEXO_DB_WRITER_LAUNCHAGENTS: tuple[str, ...] = (
    "com.nexo.local-index",
    "com.nexo.email-monitor",
    "com.nexo.followup-runner",
    "com.nexo.watchdog",
    "com.nexo.catchup",
    "com.nexo.immune",
)

NEXO_DB_WRITER_MARKERS: tuple[str, ...] = (
    "nexo-local-index.py",
    "nexo-email-monitor.py",
    "nexo-followup-runner.py",
    "nexo-catchup.py",
    "nexo-watchdog.sh",
    "nexo-immune.py",
)


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


def db_row_counts(path: str | Path, tables: tuple[str, ...] = RECOVERY_TABLES) -> dict[str, int | None]:
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
    tables: tuple[str, ...] = PROTECTED_TABLES,
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
    counts = db_row_counts(p, tables)
    if size <= EMPTY_DB_SIZE_BYTES:
        # Small but not necessarily wiped — confirm via row counts.
        return _counts_look_wiped(counts)
    return _counts_look_wiped(counts)


def _counts_look_wiped(counts: dict[str, int | None]) -> bool:
    """Treat Brain table loss as a wipe even if legacy local tables remain."""
    critical_present = {
        table: counts.get(table)
        for table in CRITICAL_TABLES
        if counts.get(table) is not None
    }
    if critical_present:
        return _all_tables_empty_or_missing(critical_present)
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
    tables: tuple[str, ...] = PROTECTED_TABLES,
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
        counts = db_row_counts(candidate, tables)
        total = sum(v for v in counts.values() if isinstance(v, int))
        if total >= min_critical_rows:
            return candidate
    return None


def find_best_hourly_backup(
    backups_dir: str | Path,
    max_age_seconds: int = HOURLY_BACKUP_MAX_AGE,
    glob: str = HOURLY_BACKUP_GLOB,
    min_critical_rows: int = 1,
    tables: tuple[str, ...] = PROTECTED_TABLES,
) -> Path | None:
    """Return the usable hourly backup with the most protected rows.

    Newest is not always safest: if an update/reset starts reindexing from
    scratch, the next hourly backups are recent but degraded. For local memory
    recovery we prefer the richest backup and use mtime only as a tie-breaker.
    """
    base = Path(backups_dir)
    if not base.is_dir():
        return None
    now = time.time()
    best: tuple[int, float, Path] | None = None
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
            continue
        counts = db_row_counts(entry, tables)
        total = sum(v for v in counts.values() if isinstance(v, int))
        if total < min_critical_rows:
            continue
        candidate = (int(total), float(stat.st_mtime), entry)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


# ── Diff & wipe detection ───────────────────────────────────────────────

def diff_row_counts(
    current: str | Path,
    reference: str | Path,
    tables: tuple[str, ...] = PROTECTED_TABLES,
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
    tables: tuple[str, ...] = PROTECTED_TABLES,
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
        if s is not None and d is not None and d < s and not _backup_drift_is_safe(s, d):
            discrepancies.append(f"{table}: source={s} backup={d}")
    if discrepancies:
        return False, "; ".join(discrepancies)
    return True, None


def _backup_drift_is_safe(source_count: int, backup_count: int) -> bool:
    """Allow tiny live-write drift while still rejecting real backup data loss.

    `sqlite3.backup()` creates a consistent snapshot, but NEXO's background
    memory service can add rows immediately after the snapshot. Comparing the
    backup with the live DB after that growth must not abort an update. Small
    tables stay exact because a 1-row loss there can be meaningful.
    """
    if backup_count <= 0 or source_count < 1000:
        return False
    drift = source_count - backup_count
    allowed = max(25, int(source_count * 0.005))
    return 0 < drift <= allowed


def _quote_identifier(identifier: str) -> str:
    if identifier not in PROTECTED_TABLES and identifier not in LOCAL_CONTEXT_TABLES:
        raise ValueError(f"refusing unsafe table identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def restore_tables_from_backup(
    source: str | Path,
    target: str | Path,
    tables: tuple[str, ...] = LOCAL_CONTEXT_TABLES,
) -> dict:
    """Replace selected tables in ``target`` with the copy from ``source``.

    This is intentionally table-scoped. It lets Doctor/repair recover days of
    local indexing from a backup without rolling back newer conversations,
    credentials, followups, or other Brain state created after that backup.
    """
    src = Path(source)
    dst = Path(target)
    result: dict = {
        "ok": False,
        "source": str(src),
        "target": str(dst),
        "tables": {},
        "errors": [],
    }
    if not src.is_file():
        result["errors"].append(f"source missing: {src}")
        return result
    if not dst.is_file():
        result["errors"].append(f"target missing: {dst}")
        return result

    conn = None
    try:
        conn = sqlite3.connect(str(dst), timeout=30)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ATTACH DATABASE ? AS backup_db", (str(src),))
        for table in tables:
            quoted = _quote_identifier(table)
            src_exists = conn.execute(
                "SELECT sql FROM backup_db.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if src_exists is None:
                result["tables"][table] = {"status": "missing_in_source"}
                continue
            dst_exists = conn.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if dst_exists is None:
                create_sql = str(src_exists[0] or "").strip()
                if not create_sql:
                    result["tables"][table] = {"status": "schema_missing_in_source"}
                    continue
                conn.execute(create_sql)
            before = _table_count(conn, table) or 0
            conn.execute(f"DELETE FROM main.{quoted}")
            conn.execute(f"INSERT INTO main.{quoted} SELECT * FROM backup_db.{quoted}")
            after = _table_count(conn, table) or 0
            result["tables"][table] = {
                "status": "restored",
                "before": int(before),
                "after": int(after),
            }
        conn.commit()
        result["ok"] = not result["errors"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.execute("DETACH DATABASE backup_db")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
    return result


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


# ── DB writer quiescence ────────────────────────────────────────────────

def quiesce_nexo_db_writers(
    dry_run: bool = False,
    *,
    stop_launchagents: bool = True,
    settle_seconds: float = 0.75,
) -> dict:
    """Stop known NEXO background writers before replacing ``nexo.db``.

    ``kill_nexo_mcp_servers`` is not enough for Desktop installs: local-index,
    email monitor, followup-runner and catchup can keep a stale DB handle open
    even after the MCP server exits. This helper is intentionally narrow and
    only targets product-owned long-lived writers.
    """
    result: dict = {
        "dry_run": dry_run,
        "mcp": {},
        "launchagents": {"stopped": [], "errors": [], "unsupported": False},
        "processes": {"scanned": 0, "terminated": 0, "pids": [], "errors": []},
        "terminated": 0,
        "errors": [],
    }

    mcp_report = kill_nexo_mcp_servers(dry_run=dry_run)
    result["mcp"] = mcp_report
    result["terminated"] += int(mcp_report.get("terminated") or 0)
    result["errors"].extend(mcp_report.get("errors") or [])

    if stop_launchagents:
        la_report = _stop_nexo_launchagents(dry_run=dry_run)
        result["launchagents"] = la_report
        result["errors"].extend(la_report.get("errors") or [])

    process_report = _terminate_nexo_db_writer_processes(dry_run=dry_run)
    result["processes"] = process_report
    result["terminated"] += int(process_report.get("terminated") or 0)
    result["errors"].extend(process_report.get("errors") or [])

    if not dry_run and (result["terminated"] or result["launchagents"].get("stopped")):
        time.sleep(max(settle_seconds, 0.0))
    return result


def resume_nexo_launchagents(labels: list[str] | tuple[str, ...] | None = None, dry_run: bool = False) -> dict:
    """Best-effort restart of LaunchAgents stopped by DB recovery."""
    result: dict = {"dry_run": dry_run, "started": [], "errors": [], "unsupported": False}
    if os.name != "posix" or sys_platform() != "darwin":
        result["unsupported"] = True
        return result
    uid = os.getuid()
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    chosen = tuple(labels or NEXO_DB_WRITER_LAUNCHAGENTS)
    for label in chosen:
        plist = launch_agents_dir / f"{label}.plist"
        if not plist.is_file():
            continue
        target = f"gui/{uid}/{label}"
        if dry_run:
            result["started"].append(label)
            continue
        try:
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            subprocess.run(
                ["launchctl", "kickstart", "-k", target],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["started"].append(label)
        except Exception as exc:
            result["errors"].append(f"{label}: {exc}")
    return result


def sys_platform() -> str:
    # Small indirection makes tests easy to monkeypatch without importing sys at
    # module import time in older runtimes.
    import sys
    return sys.platform


def _stop_nexo_launchagents(dry_run: bool = False) -> dict:
    result: dict = {"stopped": [], "errors": [], "unsupported": False}
    if os.name != "posix" or sys_platform() != "darwin":
        result["unsupported"] = True
        return result
    uid = os.getuid()
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    for label in NEXO_DB_WRITER_LAUNCHAGENTS:
        plist = launch_agents_dir / f"{label}.plist"
        if not plist.is_file():
            continue
        if dry_run:
            result["stopped"].append(label)
            continue
        try:
            proc = subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(plist)],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            result["errors"].append(f"{label}: {exc}")
            continue
        if proc.returncode == 0:
            result["stopped"].append(label)
        else:
            stderr = (proc.stderr or "").strip()
            # launchctl returns non-zero when an agent is already unloaded. That
            # is not a recovery blocker, so keep it as quiet evidence.
            if stderr and "No such process" not in stderr and "not found" not in stderr:
                result["errors"].append(f"{label}: {stderr[:200]}")
    return result


def _terminate_nexo_db_writer_processes(dry_run: bool = False) -> dict:
    result: dict = {"scanned": 0, "terminated": 0, "pids": [], "errors": [], "dry_run": dry_run}
    if os.name == "posix":
        return _terminate_posix_db_writer_processes(dry_run=dry_run)
    if os.name == "nt":
        return _terminate_windows_db_writer_processes(dry_run=dry_run)
    result["errors"].append("unsupported platform")
    return result


def _terminate_posix_db_writer_processes(dry_run: bool = False) -> dict:
    result: dict = {"scanned": 0, "terminated": 0, "pids": [], "errors": [], "dry_run": dry_run}
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        result["errors"].append(f"ps failed: {exc}")
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
        if not _looks_like_nexo_db_writer(cmd):
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
        except Exception as exc:
            result["errors"].append(f"kill {pid} failed: {exc}")
    return result


def _terminate_windows_db_writer_processes(dry_run: bool = False) -> dict:
    result: dict = {"scanned": 0, "terminated": 0, "pids": [], "errors": [], "dry_run": dry_run}
    ps_script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        result["errors"].append(f"powershell process scan failed: {exc}")
        return result
    if proc.returncode != 0:
        result["errors"].append(f"powershell exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        return result
    try:
        import json
        rows = json.loads(proc.stdout or "[]")
    except Exception as exc:
        result["errors"].append(f"process json parse failed: {exc}")
        return result
    if isinstance(rows, dict):
        rows = [rows]
    my_pid = os.getpid()
    for row in rows if isinstance(rows, list) else []:
        try:
            pid = int(row.get("ProcessId"))
        except Exception:
            continue
        if pid == my_pid:
            continue
        cmd = str(row.get("CommandLine") or "")
        if not _looks_like_nexo_db_writer(cmd):
            continue
        result["scanned"] += 1
        result["pids"].append({"pid": pid, "command": cmd[:180]})
        if dry_run:
            continue
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["terminated"] += 1
        except Exception as exc:
            result["errors"].append(f"taskkill {pid} failed: {exc}")
    return result


def _looks_like_nexo_db_writer(cmd: str) -> bool:
    if not cmd:
        return False
    lowered = cmd.lower()
    if _looks_like_nexo_mcp(cmd):
        return True
    if "nexo-cron-wrapper.sh" in lowered and any(label in lowered for label in (
        "local-index",
        "email-monitor",
        "followup-runner",
        "watchdog",
        "catchup",
        "immune",
    )):
        return True
    return any(marker in lowered for marker in NEXO_DB_WRITER_MARKERS)
