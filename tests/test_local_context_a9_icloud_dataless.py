"""Release A / A9 — iCloud dataless handling, error classification, drain rate.

macOS evicts iCloud files to the cloud and flags them SF_DATALESS. Reading such
a file faults in a download; from the headless index daemon that materialization
fails with EDEADLK, and the old code (a) tried to READ every file (storming
EDEADLK), (b) counted those as reliability errors, and (c) capped the logged
errors at 20 so the operator saw "20 errors" no matter how many thousands of
iCloud files were involved. This release:

  * detects SF_DATALESS with pure stdlib (no pyobjc) and indexes those files
    metadata-only until they are materialized,
  * classifies EDEADLK as 'offloaded' (not a reliability error),
  * removes the 20-error logging cap, and
  * exposes a backlog_drain_rate metric in status().
"""

import errno
import types
from pathlib import Path

from local_context import api
from local_context import db as lcdb
from local_context.util import now


def _schema_conn(tmp_path):
    conn = lcdb._connect(Path(tmp_path) / "a9.db")
    lcdb._ensure_schema(conn)
    return conn


# --- SF_DATALESS detection (pure stdlib) -----------------------------------

def test_is_dataless_reads_sf_dataless_flag():
    assert api._is_dataless(types.SimpleNamespace(st_flags=api.SF_DATALESS)) is True
    assert api._is_dataless(types.SimpleNamespace(st_flags=api.SF_DATALESS | 0x1)) is True
    assert api._is_dataless(types.SimpleNamespace(st_flags=0)) is False
    # st_flags missing (non-darwin stat) must be treated as not-dataless, not crash.
    assert api._is_dataless(types.SimpleNamespace()) is False


def test_sf_dataless_constant_is_the_macos_flag():
    # Whether or not the running stdlib exposes it, the value must be the macOS
    # SF_DATALESS bit so detection works on the target platform.
    assert api.SF_DATALESS == 0x40000000


# --- EDEADLK classification ------------------------------------------------

def test_is_offloaded_error_matches_edeadlk_symbolically():
    assert api._is_offloaded_error(OSError(errno.EDEADLK, "Resource deadlock avoided")) is True
    assert api._is_offloaded_error(OSError(errno.ENOENT, "missing")) is False
    assert api._is_offloaded_error(PermissionError("denied")) is False
    assert api._is_offloaded_error(ValueError("x")) is False


def test_record_scan_error_classifies_edeadlk_as_offloaded_not_error(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        stats = {"errors": 0}
        api._record_scan_error(
            conn, stats, "/Users/x/Library/Mobile Documents/com~apple~CloudDocs/a.pdf",
            "live_reconcile", OSError(errno.EDEADLK, "Resource deadlock avoided"),
        )
        assert stats.get("offloaded") == 1, "EDEADLK must count as offloaded"
        assert stats.get("errors") == 0, "offloaded files are not reliability errors"
        row = conn.execute(
            "SELECT error_code FROM local_index_errors ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["error_code"] == "offloaded"
    finally:
        conn.close()


# --- error cap removed -----------------------------------------------------

def test_record_scan_error_no_longer_caps_logged_errors_at_20(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        stats = {"errors": 0}
        for i in range(25):
            api._record_scan_error(
                conn, stats, f"/Users/x/secret-{i}.txt", "quick_index",
                PermissionError("Operation not permitted"),
            )
        assert stats["errors"] == 25
        logged = conn.execute("SELECT COUNT(*) AS c FROM local_index_errors").fetchone()["c"]
        assert logged == 25, f"expected all 25 errors logged (cap removed), got {logged}"
    finally:
        conn.close()


# --- backlog_drain_rate ----------------------------------------------------

def test_backlog_drain_rate_helper_computes_rate_and_eta(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        ts = now()
        for i in range(6):
            conn.execute(
                "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, priority, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"done-{i}", f"asset-{i}", "light_extraction", "done", 50, ts, ts),
            )
        conn.commit()
        rate = api._backlog_drain_rate(conn, pending=12, window_seconds=300)
        assert rate["completed_in_window"] == 6
        assert rate["window_seconds"] == 300
        # 6 jobs / 300 s = 0.02/s = 1.2/min
        assert abs(rate["per_minute"] - 1.2) < 1e-6
        # eta to drain 12 pending at 0.02/s = 600 s
        assert rate["eta_seconds"] == 600
    finally:
        conn.close()


def test_backlog_drain_rate_zero_rate_has_no_eta(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        rate = api._backlog_drain_rate(conn, pending=5, window_seconds=300)
        assert rate["completed_in_window"] == 0
        assert rate["per_minute"] == 0.0
        assert rate["eta_seconds"] is None
    finally:
        conn.close()


def test_status_global_exposes_backlog_drain_rate():
    api.ensure_ready()
    result = api.status()
    assert result["ok"] is True
    drain = result["global"]["backlog_drain_rate"]
    assert set(drain) >= {"window_seconds", "completed_in_window", "per_minute", "eta_seconds"}


# --- metadata-only gate for dataless files ---------------------------------

def test_process_jobs_indexes_dataless_file_metadata_only(tmp_path, monkeypatch):
    api.ensure_ready()
    conn = api._conn()
    real_file = tmp_path / "cloud.txt"
    real_file.write_text("materialized content that must NOT be read")
    ts = now()
    conn.execute(
        "INSERT INTO local_assets(asset_id, root_id, path, display_path, phase, status, privacy_class, "
        "first_seen_at, last_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a-dl", 0, str(real_file), str(real_file), "quick_index", "active", "normal", ts, ts, ts),
    )
    conn.execute(
        "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, priority, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("j-dl", "a-dl", "light_extraction", "pending", 50, ts, ts),
    )
    conn.commit()

    monkeypatch.setattr(api, "_is_dataless", lambda st: True)

    def _must_not_read(*args, **kwargs):
        raise AssertionError("extract_text must not read a dataless (offloaded) file")

    monkeypatch.setattr(api, "extract_text", _must_not_read)

    api.process_jobs(limit=10)

    asset = conn.execute("SELECT phase FROM local_assets WHERE asset_id='a-dl'").fetchone()
    assert asset["phase"] == "metadata_only"
    job = conn.execute(
        "SELECT status, last_error_code FROM local_index_jobs WHERE job_id='j-dl'"
    ).fetchone()
    assert job["status"] == "done"
    assert job["last_error_code"] == "offloaded"
    chunks = conn.execute(
        "SELECT COUNT(*) AS c FROM local_chunks WHERE asset_id='a-dl'"
    ).fetchone()["c"]
    assert chunks == 0
