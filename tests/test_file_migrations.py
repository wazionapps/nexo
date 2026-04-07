"""Tests for file-based migration runner in auto_update."""
import os
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
