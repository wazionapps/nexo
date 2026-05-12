from __future__ import annotations

from pathlib import Path

import db
import local_context
from db._schema import get_schema_version
from local_context import api


def test_local_context_migration_tables_exist():
    conn = db.get_db()
    assert get_schema_version() >= 63
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_assets'").fetchone()
    assert row is not None


def test_scan_extract_query_and_purge(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    invoice = root / "factura-portatil.txt"
    invoice.write_text("Factura del portátil BMW para Maria. Total 1200 euros.", encoding="utf-8")

    local_context.add_root(str(root))
    result = local_context.run_once(limit=20, process_limit=20)
    assert result["ok"] is True

    status = local_context.status()
    assert status["global"]["files_found"] >= 1
    assert status["support_log_available"] is True

    context = local_context.context_query("factura portatil Maria", limit=5)
    assert context["ok"] is True
    assert context["evidence_refs"]
    assert context["relations"]
    assert any("factura-portatil" in asset["path"] for asset in context["assets"])

    asset_id = context["assets"][0]["asset_id"]
    purge = local_context.purge_asset(asset_id)
    assert purge["ok"] is True
    assert local_context.get_asset(asset_id)["ok"] is False


def test_sensitive_files_are_inventory_only(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = root / ".env"
    secret.write_text("OPENAI_API_KEY=secret", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = db.get_db()
    row = conn.execute("SELECT depth, privacy_class FROM local_assets WHERE path=?", (str(secret),)).fetchone()
    assert row["depth"] == 1
    assert row["privacy_class"] == "sensitive_inventory_only"
    chunks = conn.execute("SELECT COUNT(*) AS total FROM local_chunks").fetchone()["total"]
    assert chunks == 0


def test_deleted_file_becomes_tombstone(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("hello local context", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    path.unlink()
    local_context.run_once(limit=20, process_limit=20)
    conn = db.get_db()
    row = conn.execute("SELECT status FROM local_assets WHERE display_path=?", (str(path),)).fetchone()
    assert row["status"] == "deleted"


def test_exclusion_prevents_indexing(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    ignored = root / "ignored"
    ignored.mkdir()
    path = ignored / "secret.txt"
    path.write_text("no index", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.add_exclusion(str(ignored))
    local_context.run_once(limit=20, process_limit=20)
    conn = db.get_db()
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(path),)).fetchone()
    assert row["total"] == 0


def test_noisy_dependency_trees_are_skipped_by_default(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    dependency = root / ".venv" / "lib" / "package.py"
    source = root / "src" / "app.py"
    dependency.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    dependency.write_text("print('dependency')", encoding="utf-8")
    source.write_text("print('source')", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    conn = db.get_db()
    skipped = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(dependency),)).fetchone()
    indexed = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(source),)).fetchone()
    assert skipped["total"] == 0
    assert indexed["total"] == 1


def test_default_roots_add_new_mounted_volumes_incrementally(tmp_path, monkeypatch):
    home = tmp_path / "home"
    external = tmp_path / "ExternalDrive"
    home.mkdir()
    external.mkdir()

    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])

    first = api.ensure_default_roots()
    assert first["created"] == 1

    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [str(external)])
    second = api.ensure_default_roots()

    roots = {row["root_path"] for row in api.list_roots()}
    assert second["created"] == 1
    assert api.norm_path(str(home)) in roots
    assert api.norm_path(str(external)) in roots


def test_pause_stops_scan_until_resume(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("paused scan", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.pause()
    paused = local_context.run_once(limit=20, process_limit=20)
    assert paused["scan"]["paused"] is True
    assert local_context.status()["global"]["phase"] == "paused"

    local_context.resume()
    resumed = local_context.run_once(limit=20, process_limit=20)
    assert resumed["scan"]["seen"] == 1


def test_checkpoint_advances_partial_scans(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"note-{index}.txt").write_text(f"checkpoint {index}", encoding="utf-8")

    local_context.add_root(str(root))
    first = local_context.run_once(limit=1, process_limit=10)
    second = local_context.run_once(limit=1, process_limit=10)
    third = local_context.run_once(limit=10, process_limit=10)

    assert first["scan"]["partial"] is True
    assert second["scan"]["partial"] is True
    assert third["scan"]["partial"] is False
    conn = db.get_db()
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE status='active'").fetchone()
    assert row["total"] == 3


def test_live_reconcile_marks_deleted_file_without_full_rescan(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("delete me", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    path.unlink()

    result = local_context.reconcile_live_changes(asset_limit=20, dir_limit=0, file_limit=0)

    assert result["assets"]["deleted"] == 1
    conn = db.get_db()
    row = conn.execute("SELECT status FROM local_assets WHERE display_path=?", (str(path),)).fetchone()
    assert row["status"] == "deleted"


def test_live_reconcile_reindexes_modified_file(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("first version", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = db.get_db()
    before = conn.execute("SELECT COUNT(*) AS total FROM local_asset_versions").fetchone()["total"]

    path.write_text("second version with more content", encoding="utf-8")
    result = local_context.reconcile_live_changes(asset_limit=20, dir_limit=0, file_limit=0)

    after = conn.execute("SELECT COUNT(*) AS total FROM local_asset_versions").fetchone()["total"]
    pending = conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE status='pending'").fetchone()["total"]
    assert result["assets"]["modified"] == 1
    assert after > before
    assert pending >= 1


def test_live_reconcile_discovers_new_file_in_known_directory(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    initial = root / "initial.txt"
    created = root / "created.txt"
    initial.write_text("initial", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    created.write_text("created after first scan", encoding="utf-8")

    result = local_context.reconcile_live_changes(asset_limit=0, dir_limit=20, file_limit=20)

    assert result["dirs"]["files_changed"] >= 1
    conn = db.get_db()
    row = conn.execute("SELECT status FROM local_assets WHERE display_path=?", (str(created),)).fetchone()
    assert row["status"] == "active"


def test_live_reconcile_marks_existing_files_deleted_after_exclusion(tmp_path):
    root = tmp_path / "docs"
    ignored = root / "ignored"
    ignored.mkdir(parents=True)
    path = ignored / "secret.txt"
    path.write_text("already indexed", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    local_context.add_exclusion(str(ignored))
    result = local_context.reconcile_live_changes(asset_limit=20, dir_limit=20, file_limit=20)

    assert result["assets"]["excluded"] + result["dirs"]["excluded_dirs"] >= 1
    conn = db.get_db()
    row = conn.execute("SELECT status FROM local_assets WHERE display_path=?", (str(path),)).fetchone()
    assert row["status"] == "deleted"


def test_service_config_renders_macos_and_windows():
    mac = api.render_service_config("macos")
    win = api.render_service_config("windows")
    assert mac["kind"] == "launchagent"
    assert mac["label"] == "com.nexo.local-index"
    assert "local-index.log" in mac["log_file"]
    assert win["kind"] == "scheduled_task"
    assert win["task_name"] == "NEXO Local Memory"
    assert "powershell" in win["install"]


def test_status_reports_macos_launchagent_running(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.nexo.local-index.plist").write_text("<plist />", encoding="utf-8")

    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "system_label", lambda: "macos")
    monkeypatch.setattr(
        api,
        "_command_output",
        lambda args, **kwargs: (
            0,
            "123\t0\tcom.nexo.local-index\n" if args == ["launchctl", "list"] else "",
            "",
        ),
    )

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is True
    assert result["service"]["manager"] == "launchagent"


def test_status_reports_loaded_macos_launchagent_as_operational(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.nexo.local-index.plist").write_text("<plist />", encoding="utf-8")

    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "system_label", lambda: "macos")
    monkeypatch.setattr(api, "_process_running", lambda pattern: False)
    monkeypatch.setattr(
        api,
        "_command_output",
        lambda args, **kwargs: (
            0,
            "-\t0\tcom.nexo.local-index\n" if args == ["launchctl", "list"] else "",
            "",
        ),
    )

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is False


def test_status_reports_windows_scheduled_task_running(monkeypatch):
    monkeypatch.setattr(api, "system_label", lambda: "windows")
    monkeypatch.setattr(api, "_command_output", lambda args, **kwargs: (0, "Running\n", ""))

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is True
    assert result["service"]["manager"] == "scheduled_task"
    assert result["service"]["task_name"] == "NEXO Local Memory"


def test_status_reports_ready_windows_scheduled_task_as_operational(monkeypatch):
    monkeypatch.setattr(api, "system_label", lambda: "windows")
    monkeypatch.setattr(api, "_command_output", lambda args, **kwargs: (0, "Ready\n", ""))
    monkeypatch.setattr(api, "_process_running", lambda pattern: False)

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is False


def test_model_status_has_local_fallback():
    result = api.model_status()
    assert result["ok"] is True
    assert any(model["kind"] == "deterministic_embedding" and model["state"] == "available" for model in result["models"])
