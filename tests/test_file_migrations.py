"""Tests for file-based migration runner and DB backup/restore in auto_update."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_run_file_migrations_stops_on_first_failure(tmp_path, monkeypatch):
    """When migration N fails, subsequent migrations must NOT run so that
    N is retried on next startup and no migration is permanently skipped."""
    import auto_update

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    version_file = tmp_path / "migration_version"

    # Migration 1: succeeds
    (migrations_dir / "001_ok.py").write_text(
        "import sys; sys.exit(0)\n"
    )
    # Migration 2: fails
    (migrations_dir / "002_fail.py").write_text(
        "import sys; sys.exit(1)\n"
    )
    # Migration 3: would succeed but should never run
    marker = tmp_path / "003_ran.txt"
    (migrations_dir / "003_should_not_run.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n"
    )

    monkeypatch.setattr(auto_update, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(auto_update, "MIGRATION_VERSION_FILE", version_file)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path)
    monkeypatch.setattr(auto_update, "REPO_DIR", tmp_path)

    results = auto_update.run_file_migrations()

    assert len(results) == 2
    assert results[0]["status"] == "ok"
    assert results[0]["version"] == 1
    assert results[1]["status"] == "failed"
    assert results[1]["version"] == 2

    # Version pointer should be at 1 (last success before the failure)
    assert version_file.read_text().strip() == "1"

    # Migration 3 must NOT have run
    assert not marker.exists(), "Migration 3 ran despite migration 2 failing"


def test_run_file_migrations_retries_failed_on_next_run(tmp_path, monkeypatch):
    """A failed migration should be retried on the next invocation."""
    import auto_update

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    version_file = tmp_path / "migration_version"

    # Write a failing migration
    script = migrations_dir / "001_initially_fails.py"
    script.write_text("import sys; sys.exit(1)\n")

    monkeypatch.setattr(auto_update, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(auto_update, "MIGRATION_VERSION_FILE", version_file)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path)
    monkeypatch.setattr(auto_update, "REPO_DIR", tmp_path)

    results1 = auto_update.run_file_migrations()
    assert len(results1) == 1
    assert results1[0]["status"] == "failed"

    # Now "fix" the migration
    script.write_text("import sys; sys.exit(0)\n")

    results2 = auto_update.run_file_migrations()
    assert len(results2) == 1
    assert results2[0]["status"] == "ok"
    assert version_file.read_text().strip() == "1"


def test_run_file_migrations_all_succeed(tmp_path, monkeypatch):
    """When all migrations succeed, version advances to the last one."""
    import auto_update

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    version_file = tmp_path / "migration_version"

    (migrations_dir / "001_first.py").write_text("import sys; sys.exit(0)\n")
    (migrations_dir / "002_second.py").write_text("import sys; sys.exit(0)\n")
    (migrations_dir / "003_third.py").write_text("import sys; sys.exit(0)\n")

    monkeypatch.setattr(auto_update, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(auto_update, "MIGRATION_VERSION_FILE", version_file)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path)
    monkeypatch.setattr(auto_update, "REPO_DIR", tmp_path)

    results = auto_update.run_file_migrations()

    assert len(results) == 3
    assert all(r["status"] == "ok" for r in results)
    assert version_file.read_text().strip() == "3"


# ── DB backup/restore connection safety ─────────────────────────────


def test_backup_dbs_closes_connections_on_success(tmp_path, monkeypatch):
    """_backup_dbs must close all SQLite connections even on success."""
    import auto_update

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "nexo.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.close()

    monkeypatch.setattr(auto_update, "DATA_DIR", data_dir)
    monkeypatch.setattr(auto_update, "NEXO_HOME", tmp_path)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path / "src_nonexistent")

    backup_dir = auto_update._backup_dbs()

    assert backup_dir is not None
    backup_db = os.path.join(backup_dir, "nexo.db")
    assert os.path.isfile(backup_db)

    # Verify the backup is a valid DB (connections were closed properly)
    verify = sqlite3.connect(backup_db)
    tables = verify.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    verify.close()
    assert any(row[0] == "t" for row in tables)


def test_backup_dbs_closes_connections_on_corrupt_source(tmp_path, monkeypatch):
    """_backup_dbs must close connections even when the source DB is corrupt."""
    import auto_update

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Write garbage to simulate a corrupt DB file
    db_file = data_dir / "nexo.db"
    db_file.write_bytes(b"this is not a valid sqlite database" * 100)

    monkeypatch.setattr(auto_update, "DATA_DIR", data_dir)
    monkeypatch.setattr(auto_update, "NEXO_HOME", tmp_path)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path / "src_nonexistent")

    # Should not raise — errors are logged and swallowed
    backup_dir = auto_update._backup_dbs()
    assert backup_dir is not None

    # The key assertion: after returning, the source DB should not be locked.
    # If connections leaked, this would fail on some platforms.
    verify = sqlite3.connect(str(db_file))
    verify.close()


def test_restore_dbs_handles_missing_gracefully(tmp_path, monkeypatch):
    """_restore_dbs must not crash and must close connections properly."""
    import auto_update

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    # Create valid source and backup DBs
    db_file = data_dir / "nexo.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()

    backup_file = backup_dir / "nexo.db"
    src = sqlite3.connect(str(db_file))
    dst = sqlite3.connect(str(backup_file))
    src.backup(dst)
    dst.close()
    src.close()

    monkeypatch.setattr(auto_update, "DATA_DIR", data_dir)
    monkeypatch.setattr(auto_update, "NEXO_HOME", tmp_path)
    monkeypatch.setattr(auto_update, "SRC_DIR", tmp_path / "src_nonexistent")

    # Should not raise
    auto_update._restore_dbs(str(backup_dir))

    # Verify the restore actually worked and connections are clean
    verify = sqlite3.connect(str(db_file))
    row = verify.execute("SELECT x FROM t").fetchone()
    verify.close()
    assert row[0] == 42


def test_validate_db_backup_detects_critical_table_regression(tmp_path):
    """Critical source tables with rows must not become empty in the backup."""
    import auto_update

    source_db = tmp_path / "source.db"
    backup_db = tmp_path / "backup.db"

    src = sqlite3.connect(str(source_db))
    src.execute("CREATE TABLE learnings (id INTEGER)")
    src.execute("CREATE TABLE session_diary (id INTEGER)")
    src.execute("CREATE TABLE guard_checks (id INTEGER)")
    src.execute("CREATE TABLE protocol_debt (id INTEGER)")
    src.execute("INSERT INTO learnings VALUES (1)")
    src.execute("INSERT INTO session_diary VALUES (1)")
    src.execute("INSERT INTO guard_checks VALUES (1)")
    src.execute("INSERT INTO protocol_debt VALUES (1)")
    src.commit()
    src.close()

    dst = sqlite3.connect(str(backup_db))
    dst.execute("CREATE TABLE learnings (id INTEGER)")
    dst.execute("CREATE TABLE session_diary (id INTEGER)")
    dst.execute("CREATE TABLE guard_checks (id INTEGER)")
    dst.execute("CREATE TABLE protocol_debt (id INTEGER)")
    dst.commit()
    dst.close()

    report = auto_update._validate_db_backup(source_db, backup_db)

    assert report["ok"] is False
    tables = {item["table"] for item in report["regressions"]}
    assert {"learnings", "session_diary", "guard_checks", "protocol_debt"} <= tables


def test_validate_db_backup_accepts_non_empty_critical_tables(tmp_path):
    """A valid backup must preserve non-empty critical tables."""
    import auto_update

    source_db = tmp_path / "source.db"
    backup_db = tmp_path / "backup.db"

    src = sqlite3.connect(str(source_db))
    dst = sqlite3.connect(str(backup_db))
    for conn in (src, dst):
        conn.execute("CREATE TABLE learnings (id INTEGER)")
        conn.execute("CREATE TABLE session_diary (id INTEGER)")
        conn.execute("INSERT INTO learnings VALUES (1)")
        conn.execute("INSERT INTO session_diary VALUES (1)")
        conn.commit()
    src.close()
    dst.close()

    report = auto_update._validate_db_backup(source_db, backup_db)

    assert report["ok"] is True
    assert report["regressions"] == []


def test_check_git_updates_aborts_before_pull_when_backup_invalid(monkeypatch):
    """Backup validation must gate the pull so bad snapshots never protect migrations."""
    import auto_update

    calls: list[tuple[str, ...]] = []

    def _fake_git(*args):
        calls.append(tuple(args))
        if args == ("fetch", "--quiet"):
            return 0, "", ""
        if args == ("rev-parse", "HEAD"):
            return 0, "local-head", ""
        if args == ("rev-parse", "@{u}"):
            return 0, "remote-head", ""
        if args == ("merge-base", "HEAD", "@{u}"):
            return 0, "local-head", ""
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(auto_update, "_git", _fake_git)
    monkeypatch.setattr(auto_update, "_read_package_version", lambda: "5.3.11")
    monkeypatch.setattr(auto_update, "_requirements_hash", lambda: "same-hash")
    monkeypatch.setattr(
        auto_update,
        "_create_validated_db_backup",
        lambda: ("/tmp/pre-autoupdate", {"ok": False, "regressions": [{"table": "learnings", "source": 212, "backup": 0}]}),
    )

    assert auto_update._check_git_updates() is None
    assert ("pull", "--ff-only") not in calls
