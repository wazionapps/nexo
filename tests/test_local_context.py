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


def test_service_config_renders_macos_and_windows():
    mac = api.render_service_config("macos")
    win = api.render_service_config("windows")
    assert mac["kind"] == "launchagent"
    assert mac["label"] == "com.nexo.local-index"
    assert "local-index.log" in mac["log_file"]
    assert win["kind"] == "scheduled_task"
    assert win["task_name"] == "NEXO Local Memory"
    assert "powershell" in win["install"]


def test_model_status_has_local_fallback():
    result = api.model_status()
    assert result["ok"] is True
    assert any(model["kind"] == "deterministic_embedding" and model["state"] == "available" for model in result["models"])
