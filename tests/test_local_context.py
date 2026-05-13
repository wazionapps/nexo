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
    assert any("factura-portatil" in asset["display_path"] for asset in context["assets"])
    assert all("path" not in asset for asset in context["assets"])

    asset_id = context["assets"][0]["asset_id"]
    purge = local_context.purge_asset(asset_id)
    assert purge["ok"] is True
    assert local_context.get_asset(asset_id)["ok"] is False


def test_status_reports_elapsed_and_eta_for_active_index(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"doc-{index}.txt").write_text(f"Documento {index} sobre Maria y BMW.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=1)
    conn = db.get_db()
    conn.execute("UPDATE local_index_logs SET created_at=1000")
    conn.commit()
    monkeypatch.setattr(api, "now", lambda: 1600)

    status = local_context.status()

    assert status["global"]["elapsed_seconds"] == 600
    assert status["global"]["files_processed"] >= 1
    assert status["global"]["changes_pending"] >= 1
    assert status["global"]["eta_seconds"] and status["global"]["eta_seconds"] > 0


def test_sensitive_files_are_not_indexed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = root / ".env"
    secret.write_text("OPENAI_API_KEY=secret", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = db.get_db()
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(secret),)).fetchone()
    assert row["total"] == 0
    chunks = conn.execute("SELECT COUNT(*) AS total FROM local_chunks").fetchone()["total"]
    assert chunks == 0


def test_private_profile_and_credential_files_are_not_indexed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("local_context.privacy.Path.home", staticmethod(lambda: home))

    files = [
        home / ".npmrc",
        home / ".boto",
        home / ".claude.json",
        home / ".grunt-init" / "jquery" / "qunit.js",
        home / ".nexo" / "data" / "secret.txt",
        home / "Documents" / "project" / ".mcp.json",
        home / "Documents" / "shopify-app" / ".shopify" / "deploy-bundle" / "manifest.json",
        home / "$tmp" / "runtime" / "note.py",
        home / "~$tmp.docx",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private token secret", encoding="utf-8")
    normal = home / "Documents" / "factura.txt"
    normal.parent.mkdir(parents=True, exist_ok=True)
    normal.write_text("Factura normal de Maria", encoding="utf-8")

    local_context.add_root(str(home))
    local_context.run_once(limit=100, process_limit=100)

    conn = db.get_db()
    for path in files:
        row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(path),)).fetchone()
        assert row["total"] == 0
    indexed = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(normal),)).fetchone()
    assert indexed["total"] == 1


def test_windows_style_private_paths_are_blocked():
    from local_context.privacy import should_skip_file, should_skip_tree

    assert should_skip_file(r"C:\Users\me\.boto\credentials") is True
    assert should_skip_file(r"C:\Users\me\AppData\Roaming\npm\.npmrc") is True
    assert should_skip_tree(r"C:\Users\me\.nexo\data") is True


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


def test_installer_volume_is_not_a_default_mounted_root(tmp_path):
    installer = tmp_path / "NEXO Desktop"
    external = tmp_path / "ExternalDrive"
    installer.mkdir()
    external.mkdir()

    assert api._should_skip_mounted_root(installer) is True
    assert api._should_skip_mounted_root(external) is False


def test_remove_root_purges_stale_assets_jobs_and_errors(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("stale removed root", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = db.get_db()
    asset = conn.execute("SELECT asset_id FROM local_assets WHERE path=?", (str(note),)).fetchone()
    assert asset is not None
    conn.execute(
        """
        INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
        VALUES (?, ?, 'light_extraction', 'TestError', 'test', 'test', 1, 1)
        """,
        (asset["asset_id"], str(note)),
    )
    conn.commit()

    result = local_context.remove_root(str(root))

    assert result["cleanup"]["assets"] >= 1
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(note),)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE asset_id=?", (asset["asset_id"],)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_errors WHERE asset_id=?", (asset["asset_id"],)).fetchone()["total"] == 0


def test_status_ignores_removed_root_residue(tmp_path):
    active_root = tmp_path / "active"
    removed_root = tmp_path / "removed"
    active_root.mkdir()
    removed_root.mkdir()

    local_context.add_root(str(active_root))
    local_context.add_root(str(removed_root))
    conn = db.get_db()
    removed = conn.execute("SELECT id FROM local_index_roots WHERE root_path=?", (api.norm_path(str(removed_root)),)).fetchone()
    conn.execute("UPDATE local_index_roots SET status='removed' WHERE id=?", (removed["id"],))
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, status, first_seen_at, last_seen_at, updated_at)
        VALUES ('asset_removed', ?, ?, ?, ?, '/', 'active', 1, 1, 1)
        """,
        (removed["id"], str(removed_root / "ghost.txt"), str(removed_root / "ghost.txt"), str(removed_root)),
    )
    conn.execute(
        "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, created_at, updated_at) VALUES ('job_removed', 'asset_removed', 'graph', 'failed', 1, 1)"
    )
    conn.execute(
        """
        INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
        VALUES ('asset_removed', ?, 'graph', 'RemovedRoot', 'removed', 'removed', 1, 1)
        """,
        (str(removed_root / "ghost.txt"),),
    )
    conn.commit()

    result = local_context.status()

    assert result["global"]["jobs_failed"] == 0
    assert result["global"]["files_found"] == 0
    assert not any(problem["support_code"] == "RemovedRoot" for problem in result["problems"])


def test_doctor_local_index_hygiene_repairs_removed_root_residue(tmp_path):
    from doctor.providers.runtime import check_local_index_hygiene

    removed_root = tmp_path / "NEXO Desktop"
    removed_root.mkdir()
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO local_index_roots(root_path, display_path, mode, depth, status, created_at, updated_at)
        VALUES (?, ?, 'normal', 2, 'removed', 1, 1)
        """,
        (api.norm_path(str(removed_root)), str(removed_root)),
    )
    root_id = conn.execute("SELECT id FROM local_index_roots WHERE root_path=?", (api.norm_path(str(removed_root)),)).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, status, first_seen_at, last_seen_at, updated_at)
        VALUES ('asset_removed', ?, ?, ?, ?, '/', 'active', 1, 1, 1)
        """,
        (root_id, str(removed_root / "ghost.txt"), str(removed_root / "ghost.txt"), str(removed_root)),
    )
    conn.execute(
        "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, created_at, updated_at) VALUES ('job_removed', 'asset_removed', 'graph', 'failed', 1, 1)"
    )
    conn.execute(
        """
        INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
        VALUES ('asset_removed', ?, 'graph', 'RemovedRoot', 'removed', 'removed', 1, 1)
        """,
        (str(removed_root / "ghost.txt"),),
    )
    conn.commit()

    dry = check_local_index_hygiene(fix=False)
    fixed = check_local_index_hygiene(fix=True)

    assert dry.status == "degraded"
    assert fixed.status == "healthy"
    assert fixed.fixed is True
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE root_id=?", (root_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE asset_id='asset_removed'").fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_errors WHERE asset_id='asset_removed'").fetchone()["total"] == 0


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
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE display_path=?", (str(path),)).fetchone()
    assert row["total"] == 0


def test_privacy_hygiene_purges_existing_private_payloads(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("local_context.privacy.Path.home", staticmethod(lambda: home))
    private = home / ".claude.json"
    private.write_text("token secret", encoding="utf-8")
    normal = home / "Documents" / "note.txt"
    normal.parent.mkdir(parents=True)
    normal.write_text("normal note", encoding="utf-8")

    local_context.add_root(str(home))
    conn = db.get_db()
    root_id = conn.execute("SELECT id FROM local_index_roots WHERE root_path=?", (api.norm_path(str(home)),)).fetchone()["id"]
    asset_id = api.stable_id("asset", api.norm_path(str(private)))
    version_id = api.stable_id("ver", asset_id)
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, file_type, extension,
          size_bytes, quick_fingerprint, depth, depth_reason, phase, status, privacy_class, permission_state,
          first_seen_at, last_seen_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '/', 'document', '.json', 1, 'old', 2, 'old', 'embeddings', 'active', 'normal', 'granted', 1, 1, 1)
        """,
        (asset_id, root_id, str(private), str(private), str(private.parent)),
    )
    conn.execute(
        "INSERT INTO local_asset_versions(version_id, asset_id, quick_fingerprint, content_hash, size_bytes, modified_at_fs, summary, created_at) VALUES (?, ?, 'old', '', 1, 1, 'secret', 1)",
        (version_id, asset_id),
    )
    conn.execute(
        "INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) VALUES ('chunk_private', ?, ?, 0, 'secret token', 2, 1)",
        (asset_id, version_id),
    )
    conn.execute(
        "INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at) VALUES ('emb_private', ?, 'chunk_private', 'm', 'r', 1, '[1]', 1)",
        (asset_id,),
    )
    conn.execute(
        "INSERT INTO local_entities(entity_id, asset_id, version_id, name, entity_type, confidence, evidence, created_at) VALUES ('ent_private', ?, ?, 'Secret', 'entity', 1, '', 1)",
        (asset_id, version_id),
    )
    conn.execute(
        "INSERT INTO local_relations(relation_id, source_asset_id, target_asset_id, target_ref, relation_type, confidence, evidence, active, created_at) VALUES ('rel_private', ?, ?, ?, 'test', 1, '', 1, 1)",
        (asset_id, asset_id, asset_id),
    )
    conn.execute(
        "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, created_at, updated_at) VALUES ('job_private', ?, 'graph', 'pending', 1, 1)",
        (asset_id,),
    )
    conn.commit()

    result = api.local_index_privacy_hygiene(fix=True)

    assert result["cleanup"]["assets"] == 1
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_chunks WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_embeddings WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_entities WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_relations WHERE source_asset_id=? OR target_asset_id=? OR target_ref=?", (asset_id, asset_id, asset_id)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0


def test_privacy_hygiene_blocks_secrets_in_late_chunks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "app.php"
    path.write_text("safe file", encoding="utf-8")

    local_context.add_root(str(root))
    conn = db.get_db()
    root_id = conn.execute("SELECT id FROM local_index_roots WHERE root_path=?", (api.norm_path(str(root)),)).fetchone()["id"]
    asset_id = api.stable_id("asset", api.norm_path(str(path)))
    version_id = api.stable_id("ver", asset_id)
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, file_type, extension,
          size_bytes, quick_fingerprint, depth, depth_reason, phase, status, privacy_class, permission_state,
          first_seen_at, last_seen_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '/', 'code', '.php', 1, 'old', 5, 'old', 'embeddings', 'active', 'normal', 'granted', 1, 1, 1)
        """,
        (asset_id, root_id, str(path), str(path), str(path.parent)),
    )
    conn.execute(
        "INSERT INTO local_asset_versions(version_id, asset_id, quick_fingerprint, content_hash, size_bytes, modified_at_fs, summary, created_at) VALUES (?, ?, 'old', '', 1, 1, 'summary', 1)",
        (version_id, asset_id),
    )
    for idx in range(30):
        conn.execute(
            "INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) VALUES (?, ?, ?, ?, ?, 2, 1)",
            (f"chunk_safe_{idx}", asset_id, version_id, idx, f"safe chunk {idx}"),
        )
    conn.execute(
        "INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) VALUES ('chunk_secret_late', ?, ?, 30, ?, 2, 1)",
        (asset_id, version_id, 'ShopInternalAccessToken = "993bbecc13b61ea9a1b6c8d467b4b8eeb681d5a36fc6d575e2fd361e0dd74482ac3cee59f07f1237036fc5c2381673919407";'),
    )
    conn.execute(
        "INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at) VALUES ('emb_late_secret', ?, 'chunk_secret_late', 'm', 'r', 1, '[1]', 1)",
        (asset_id,),
    )
    conn.execute(
        "INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, created_at, updated_at) VALUES ('job_late_secret', ?, 'graph', 'pending', 1, 1)",
        (asset_id,),
    )
    conn.commit()

    result = api.local_index_privacy_hygiene(fix=True)

    assert result["cleanup"]["content_secret_assets"] == 1
    row = conn.execute("SELECT privacy_class, phase FROM local_assets WHERE asset_id=?", (asset_id,)).fetchone()
    assert row["privacy_class"] == "content_secret_inventory_only"
    assert row["phase"] == "privacy_blocked"
    assert conn.execute("SELECT COUNT(*) AS total FROM local_chunks WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_embeddings WHERE asset_id=?", (asset_id,)).fetchone()["total"] == 0


def test_secret_bearing_content_is_inventory_only_not_queryable(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    token_file = root / "sendwa.php"
    token_file.write_text(
        'ShopInternalAccessToken = "993bbecc13b61ea9a1b6c8d467b4b8eeb681d5a36fc6d575e2fd361e0dd74482ac3cee59f07f1237036fc5c2381673919407";',
        encoding="utf-8",
    )

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    conn = db.get_db()
    row = conn.execute("SELECT privacy_class, phase FROM local_assets WHERE path=?", (str(token_file),)).fetchone()
    assert row["privacy_class"] == "content_secret_inventory_only"
    assert row["phase"] == "privacy_blocked"
    assert conn.execute("SELECT COUNT(*) AS total FROM local_chunks").fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_embeddings").fetchone()["total"] == 0

    query = local_context.context_query("sendwa", limit=5)
    assert query["assets"] == []
    assert query["warnings"]


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
