"""Regression test for the cron wrapper's disabled-gate behaviour.

``src/scripts/nexo-cron-wrapper.sh`` contains an inline Python fragment
(Plan F0.2.4 "disabled-script gate") that short-circuits to exit 0 with
summary ``[disabled]`` whenever the operator has flipped
``personal_scripts.enabled`` to 0 via ``nexo scripts disable <name>``.
AUDITOR-V650-PASS1 §7 flagged that there was no Python-side regression
covering this gate. Exercising the embedded fragment end-to-end in a
subprocess via ``bash`` pulls in too many runtime deps for CI, so the
test instead extracts the canonical SQL + branching rule and asserts
they match the wrapper source.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "src" / "scripts" / "nexo-cron-wrapper.sh"


def _extract_gate_snippet() -> str:
    """Return the Python block between <<'PYGATE' and PYGATE inside the wrapper."""
    text = WRAPPER.read_text()
    match = re.search(r"<<'PYGATE'.*?\n(.*?)^PYGATE", text, re.DOTALL | re.MULTILINE)
    assert match is not None, "could not locate PYGATE block in nexo-cron-wrapper.sh"
    return match.group(1)


def test_gate_snippet_reads_personal_scripts_enabled_only():
    """The gate must look up ``enabled`` keyed on script ``name`` and do
    nothing else — no joins, no writes — so the wrapper stays fast on
    every tick."""
    snippet = _extract_gate_snippet()
    # Must query personal_scripts.enabled keyed on name (case-insensitive
    # match on the column because the row lookup is what matters here).
    assert re.search(
        r"SELECT\s+enabled\s+FROM\s+personal_scripts\s+WHERE\s+name\s*=\s*\?",
        snippet,
        flags=re.IGNORECASE,
    ), "gate must read personal_scripts.enabled by script name"
    # Must NOT issue any UPDATE / DELETE / INSERT — read-only gate.
    assert not re.search(r"\b(UPDATE|DELETE|INSERT)\b", snippet, flags=re.IGNORECASE), (
        "disabled-gate SQL must stay read-only; mutations belong in cron-wrapper.sh body"
    )


def _run_gate(db_path: Path, cron_id: str) -> subprocess.CompletedProcess:
    """Execute the embedded gate snippet against ``db_path``/``cron_id`` and
    return the CompletedProcess so the caller can inspect stdout."""
    snippet = _extract_gate_snippet()
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, "-", str(db_path), cron_id],
        input=snippet,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "nexo.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE personal_scripts (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO personal_scripts (name, enabled) VALUES (?, ?)",
        [
            ("morning-agent", 1),
            ("email-monitor", 0),
            ("followup-runner", 1),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_enabled_script_emits_nothing(seeded_db):
    result = _run_gate(seeded_db, "morning-agent")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_disabled_script_emits_marker(seeded_db):
    result = _run_gate(seeded_db, "email-monitor")
    assert result.returncode == 0
    assert result.stdout.strip() == "disabled"


def test_unknown_script_emits_nothing(seeded_db):
    """Row missing altogether → no disabled marker; the wrapper lets the
    cron run so a newly-installed script is not silently skipped."""
    result = _run_gate(seeded_db, "does-not-exist")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_db_bails_quietly():
    """If the DB is absent the gate must not fail the cron; it prints
    nothing and exits 0 so the wrapper falls through to the real run."""
    result = _run_gate(Path("/tmp/__definitely-not-a-real-nexo-db__.sqlite"), "morning-agent")
    assert result.returncode == 0
    assert result.stdout.strip() == ""
