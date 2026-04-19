#!/usr/bin/env python3
"""Backfill task.owner for legacy followups/reminders with owner IS NULL.

Lets Desktop drop its client-side `_legacyClassifyOwner` regex fallback
(main.js) by persisting an owner token on every legacy row. The Brain
core stays neutral on NEW tasks (v5.8.2 contract — no automatic
classification on create); this script runs ONCE per install/upgrade to
clear the backlog of pre-classification rows.

Rules applied (in order):
  1. id starts with NF-PROTOCOL-       -> 'user'  (user-decision items)
  2. category == 'waiting'             -> 'waiting'
  3. recurrence is non-empty           -> 'agent'
  4. description matches user-verb     -> 'user'
     (e.g. "Francisco decide", "Francisco revisa", "revisar", "aprobar",
      "approve", "review", "decide")
  5. description matches waiting-for   -> 'waiting'
     (e.g. "esperando respuesta", "waiting for", "cuando X responda")
  6. description matches agent-verb    -> 'agent'
     (e.g. "cron", "auto", "verifica cada", "check every", "monitoriza")
  7. otherwise                         -> 'shared'

User verbs are localized via calibration.json.user.name so "Francisco decide"
is treated like "<name> decide" for any deployment.

Usage:
    python3 scripts/backfill_task_owner.py --dry-run
    python3 scripts/backfill_task_owner.py              # writes with backup
    python3 scripts/backfill_task_owner.py --no-backup  # trust the caller
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path


DEFAULT_DB_PATH = Path.home() / ".nexo" / "runtime" / "data" / "nexo.db"
DEFAULT_CALIBRATION = Path.home() / ".nexo" / "brain" / "calibration.json"

_WAITING_PHRASES = (
    r"esperando (?:respuesta|a\s+\w+|confirmaci[oó]n)",
    r"cuando\s+\w+\s+responda",
    r"waiting\s+(?:for|on)\s+\w+",
    r"pendiente\s+de\s+\w+",
)

_AGENT_PHRASES = (
    r"\bcron\b",
    r"\bauto(?:-|\s)?ejecut\w*",
    r"\bauto(?:-|\s)?run\b",
    r"verifica\s+cada\b",
    r"check\s+every\b",
    r"monitor(?:iz|iz\w+)\b",
    r"run\s+every\b",
    r"ejecuta\s+cada\b",
    r"schedule(?:d)?\s+\w+",
)

_USER_VERBS_ES = (
    "decide", "decidir", "revisa", "revisar", "aprueba", "aprobar",
    "confirma", "confirmar", "elige", "elegir", "valida", "validar",
    "responde", "responder",
)
_USER_VERBS_EN = (
    "decide", "decides", "review", "reviews", "approve", "approves",
    "confirm", "confirms", "choose", "chooses", "validate", "validates",
    "answer", "answers",
)


def _compile(rxs):
    return [re.compile(rx, re.IGNORECASE) for rx in rxs]


def _load_user_name(calibration_path: Path) -> str:
    try:
        data = json.loads(calibration_path.read_text())
        return str((data.get("user") or {}).get("name") or "").strip()
    except Exception:
        return ""


def classify(
    *,
    item_id: str,
    description: str,
    category: str,
    recurrence: str,
    user_name: str,
) -> str:
    """Return one of 'user', 'waiting', 'agent', 'shared'."""
    tid = (item_id or "").strip().lower()
    if tid.startswith("nf-protocol-"):
        return "user"

    cat = (category or "").strip().lower()
    if cat == "waiting":
        return "waiting"

    if (recurrence or "").strip():
        return "agent"

    desc = description or ""
    desc_low = desc.lower()

    if user_name:
        name_low = user_name.lower()
        user_verbs = "|".join(re.escape(v) for v in _USER_VERBS_ES + _USER_VERBS_EN)
        name_verb_rx = re.compile(
            rf"\b{re.escape(name_low)}\s+(?:{user_verbs})\b",
            re.IGNORECASE,
        )
        if name_verb_rx.search(desc_low):
            return "user"

    for rx in _compile(_WAITING_PHRASES):
        if rx.search(desc_low):
            return "waiting"

    for rx in _compile(_AGENT_PHRASES):
        if rx.search(desc_low):
            return "agent"

    imperative_rx = re.compile(
        r"^(?:revisar|aprobar|decidir|confirmar|validar|elegir|responder|"
        r"review|approve|decide|confirm|validate|choose|answer)\b",
        re.IGNORECASE,
    )
    if imperative_rx.search(desc_low):
        return "user"

    return "shared"


def _select_null_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    has_category: bool,
    has_recurrence: bool,
):
    cols = ["id", "description"]
    if has_recurrence:
        cols.append("recurrence")
    if has_category:
        cols.append("category")
    sql = f"SELECT {', '.join(cols)} FROM {table} WHERE owner IS NULL OR owner = ''"
    cur = conn.execute(sql)
    results = []
    for row in cur.fetchall():
        d = {"id": row[0], "description": row[1] or "", "recurrence": "", "category": ""}
        idx = 2
        if has_recurrence:
            d["recurrence"] = row[idx] or ""
            idx += 1
        if has_category:
            d["category"] = row[idx] or ""
        results.append(d)
    return results


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


def _backup_db(db_path: Path) -> Path:
    ts = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
    backup = db_path.parent.parent / "backups" / f"pre-backfill-owner-{ts}" / db_path.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup)
    return backup


def run(
    db_path: Path,
    calibration_path: Path,
    *,
    dry_run: bool,
    do_backup: bool,
) -> dict:
    if not db_path.exists():
        raise SystemExit(f"nexo.db not found at {db_path}")
    user_name = _load_user_name(calibration_path)

    conn = sqlite3.connect(str(db_path))
    try:
        plans = []
        for table in ("followups", "reminders"):
            if not _has_column(conn, table, "owner"):
                continue
            has_category = _has_column(conn, table, "category")
            has_recurrence = _has_column(conn, table, "recurrence")
            for row in _select_null_rows(
                conn, table, has_category=has_category, has_recurrence=has_recurrence
            ):
                owner = classify(
                    item_id=row["id"],
                    description=row["description"],
                    category=row["category"],
                    recurrence=row["recurrence"],
                    user_name=user_name,
                )
                plans.append({"table": table, "id": row["id"], "owner": owner})

        totals = {"user": 0, "waiting": 0, "agent": 0, "shared": 0}
        for p in plans:
            totals[p["owner"]] += 1

        report = {
            "db_path": str(db_path),
            "user_name": user_name,
            "dry_run": dry_run,
            "rows_found": len(plans),
            "by_owner": totals,
        }

        if dry_run or not plans:
            return report

        if do_backup:
            report["backup_path"] = str(_backup_db(db_path))

        conn.execute("BEGIN IMMEDIATE")
        for p in plans:
            conn.execute(
                f"UPDATE {p['table']} SET owner = ? WHERE id = ? AND (owner IS NULL OR owner = '')",
                (p["owner"], p["id"]),
            )
        conn.commit()
        report["rows_updated"] = len(plans)
        return report
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--calibration", default=str(DEFAULT_CALIBRATION))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args(argv)

    report = run(
        Path(args.db),
        Path(args.calibration),
        dry_run=args.dry_run,
        do_backup=not args.no_backup,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
