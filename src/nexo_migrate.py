"""nexo_migrate — Plan Consolidado F0.0 migration helper.

Pre-requisite for F0.1→F0.6 (the scripts-classification / core-vs-personal
reshuffle). This module owns:

  - ``apply_migration(version, fn, notes="")`` — idempotent runner that
    records success in ``migrations_applied`` and writes the matching
    ``~/.nexo/.structure-version`` file so ``doctor`` and the CLI can
    detect where the runtime is in the migration ladder.
  - ``get_structure_version()`` — reader for the .structure-version file.
  - ``ensure_migrations_table(conn)`` — idempotent ``CREATE TABLE IF NOT
    EXISTS``.
  - ``is_applied(version, conn=None)`` — check before re-running.

The guardian hook (``hooks/protocol-pretool-guardrail.sh`` /
``hook_guardrails.py``) already recognises the ``NEXO_MIGRATING=1``
environment variable; this helper sets it for the duration of
``apply_migration`` so live-repo writes during a fase are not blocked
by learnings that guard those paths.

Fail-closed: a migration that throws leaves the old structure-version
file in place and does NOT record the version as applied — rollback is
implicit.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Callable


def _nexo_home() -> Path:
    env = os.environ.get("NEXO_HOME")
    if env:
        return Path(env)
    return Path.home() / ".nexo"


def _db_path() -> Path:
    env = os.environ.get("NEXO_DB_PATH")
    if env:
        return Path(env)
    return _nexo_home() / "data" / "nexo.db"


def _structure_version_path() -> Path:
    return _nexo_home() / ".structure-version"


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migrations_applied (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            notes      TEXT
        )
        """
    )
    conn.commit()


def is_applied(version: str, *, conn: sqlite3.Connection | None = None) -> bool:
    owned = False
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        owned = True
    try:
        ensure_migrations_table(conn)
        cur = conn.execute(
            "SELECT 1 FROM migrations_applied WHERE version = ?",
            (version,),
        )
        return cur.fetchone() is not None
    finally:
        if owned:
            conn.close()


def _record_applied(version: str, notes: str, conn: sqlite3.Connection) -> None:
    ensure_migrations_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO migrations_applied(version, applied_at, notes) VALUES (?, ?, ?)",
        (version, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), notes or ""),
    )
    conn.commit()


def apply_migration(
    version: str,
    fn: Callable[[sqlite3.Connection], None],
    *,
    notes: str = "",
    db_path: Path | None = None,
) -> dict:
    """Run ``fn(conn)`` under the NEXO_MIGRATING flag and record success.

    Idempotent: already-applied versions return early with
    ``{"applied": False, "reason": "already_applied"}``. A failing ``fn``
    propagates its exception AFTER rolling back the transaction and
    clears the NEXO_MIGRATING flag even on error.
    """
    path = Path(db_path) if db_path is not None else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_flag = os.environ.get("NEXO_MIGRATING")
    os.environ["NEXO_MIGRATING"] = "1"
    try:
        with sqlite3.connect(str(path)) as conn:
            if is_applied(version, conn=conn):
                return {"applied": False, "version": version, "reason": "already_applied"}
            try:
                fn(conn)
                _record_applied(version, notes, conn)
                _structure_version_path().parent.mkdir(parents=True, exist_ok=True)
                _structure_version_path().write_text(version + "\n", encoding="utf-8")
                return {"applied": True, "version": version, "notes": notes}
            except Exception:
                conn.rollback()
                raise
    finally:
        if prev_flag is None:
            os.environ.pop("NEXO_MIGRATING", None)
        else:
            os.environ["NEXO_MIGRATING"] = prev_flag


def get_structure_version() -> str:
    try:
        return _structure_version_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def bootstrap_f00(*, db_path: Path | None = None) -> dict:
    """Convenience: install the migrations_applied table + F0.0 marker.

    Safe to call repeatedly; follows the idempotent apply_migration path.
    """
    def _noop(_conn):
        # F0.0 is a bootstrap marker — the side-effect is just "the
        # migrations_applied table exists and we recorded F0.0". The
        # real schema ALTERs live in later versions (F0.1+).
        pass

    return apply_migration("F0.0", _noop, notes="bootstrap migrations_applied", db_path=db_path)


__all__ = [
    "apply_migration",
    "bootstrap_f00",
    "ensure_migrations_table",
    "get_structure_version",
    "is_applied",
]
