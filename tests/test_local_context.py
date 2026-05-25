from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import local_context
from db import get_db
from db._schema import get_schema_version
from local_context import api
from local_context.db import (
    MAIN_CLEANUP_STATE_KEY,
    MIGRATION_STATE_KEY,
    close_local_context_db,
    get_local_context_db,
    local_context_db_path,
)


def test_local_context_migration_tables_exist():
    conn = get_local_context_db()
    assert get_schema_version() >= 63
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_assets'").fetchone()
    assert row is not None


def test_local_context_repairs_legacy_sidecar_v2_columns_before_source_indexes():
    close_local_context_db()
    db_path = local_context_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(db_path)
    try:
        raw.executescript(
            """
            CREATE TABLE local_index_roots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL UNIQUE,
                display_path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'normal',
                depth INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'active',
                last_scan_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE local_index_exclusions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                display_path TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'user',
                created_at REAL NOT NULL
            );
            INSERT INTO local_index_roots(root_path, display_path, mode, depth, status, created_at, updated_at)
            VALUES ('/legacy-root', '/legacy-root', 'normal', 2, 'active', 1, 1);
            INSERT INTO local_index_exclusions(path, display_path, reason, created_at)
            VALUES ('/legacy-root/System', '/legacy-root/System', 'legacy', 1);
            """
        )
        raw.commit()
    finally:
        raw.close()

    conn = get_local_context_db()

    root_columns = {row["name"] for row in conn.execute("PRAGMA table_info(local_index_roots)").fetchall()}
    exclusion_columns = {row["name"] for row in conn.execute("PRAGMA table_info(local_index_exclusions)").fetchall()}
    assert {"source", "remote", "seed_version"}.issubset(root_columns)
    assert {"source", "kind"}.issubset(exclusion_columns)

    root = conn.execute("SELECT source, remote, seed_version FROM local_index_roots WHERE root_path='/legacy-root'").fetchone()
    exclusion = conn.execute("SELECT source, kind FROM local_index_exclusions WHERE path='/legacy-root/System'").fetchone()
    assert dict(root) == {"source": "legacy", "remote": 0, "seed_version": 1}
    assert dict(exclusion) == {"source": "legacy", "kind": "folder"}
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_local_index_roots_source'").fetchone()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_local_index_exclusions_source'").fetchone()


def test_local_context_migrates_legacy_rows_out_of_main_db():
    main = get_db()
    main.execute(
        """
        INSERT OR REPLACE INTO local_index_state(key, value, updated_at)
        VALUES ('legacy_probe', 'yes', 1)
        """
    )
    main.commit()

    local = get_local_context_db()

    migrated = local.execute("SELECT value FROM local_index_state WHERE key='legacy_probe'").fetchone()
    assert migrated is not None
    assert migrated["value"] == "yes"
    assert main.execute("SELECT COUNT(*) AS total FROM local_index_state WHERE key='legacy_probe'").fetchone()["total"] == 0
    assert local.execute("SELECT value FROM local_index_state WHERE key=?", (MAIN_CLEANUP_STATE_KEY,)).fetchone()


def test_local_context_retries_main_drain_when_marker_already_exists():
    local = get_local_context_db()
    local.execute(
        """
        INSERT OR REPLACE INTO local_index_state(key, value, updated_at)
        VALUES (?, 'old-marker', 1)
        """,
        (MIGRATION_STATE_KEY,),
    )
    local.commit()
    close_local_context_db()

    main = get_db()
    main.execute(
        """
        INSERT OR REPLACE INTO local_index_state(key, value, updated_at)
        VALUES ('legacy_after_marker', 'pending', 1)
        """
    )
    main.commit()

    local = get_local_context_db()

    migrated = local.execute("SELECT value FROM local_index_state WHERE key='legacy_after_marker'").fetchone()
    assert migrated is not None
    assert migrated["value"] == "pending"
    assert main.execute("SELECT COUNT(*) AS total FROM local_index_state WHERE key='legacy_after_marker'").fetchone()["total"] == 0


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


def test_initial_scan_discovers_known_documents_before_unknown_files(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    unknown = root / "aaa-cache.bin"
    document = root / "zzz-contrato.pdf"
    unknown.write_bytes(b"binary cache")
    document.write_bytes(b"%PDF-1.4 fake contract")

    local_context.add_root(str(root))
    result = local_context.run_once(limit=1, process_limit=0)

    assert result["scan"]["seen"] == 1
    conn = get_local_context_db()
    rows = conn.execute("SELECT path FROM local_assets").fetchall()
    assert [Path(row["path"]).name for row in rows] == [document.name]


def test_extraction_jobs_prioritize_known_document_formats(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    files = {
        "contract.pdf": b"%PDF-1.4 fake contract",
        "brief.docx": b"fake docx",
        "sheet.xlsx": b"fake xlsx",
        "note.txt": b"plain note",
        "message.eml": b"Subject: Hello\n\nBody",
        "script.py": b"print('noise')",
    }
    for name, body in files.items():
        (root / name).write_bytes(body)

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = get_local_context_db()
    rows = conn.execute(
        """
        SELECT a.path, j.priority
        FROM local_index_jobs j
        JOIN local_assets a ON a.asset_id=j.asset_id
        WHERE j.job_type='light_extraction'
        ORDER BY j.priority DESC, a.path ASC
        """
    ).fetchall()

    priorities = {Path(row["path"]).suffix: int(row["priority"]) for row in rows}
    assert priorities[".pdf"] == 90
    assert priorities[".docx"] == 90
    assert priorities[".xlsx"] == 90
    assert priorities[".txt"] == 82
    assert priorities[".eml"] == 70
    assert priorities[".py"] == 55
    assert [Path(row["path"]).suffix for row in rows[:3]] == [".docx", ".pdf", ".xlsx"]


def test_local_memory_read_paths_use_readonly_sidecar_without_prepare_or_audit_write(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "sidecar-memory.txt"
    note.write_text("Memoria local sidecar conectada con evidencia BMW Maria.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
    before_queries = conn.execute("SELECT COUNT(*) AS total FROM local_context_queries").fetchone()["total"]

    def fail_if_write_prepare_runs():
        raise AssertionError("read-only local memory path must not prepare or migrate the database")

    monkeypatch.setattr(api, "ensure_ready", fail_if_write_prepare_runs)

    context = local_context.context_query("sidecar BMW Maria", limit=5)
    roots = local_context.list_roots()
    exclusions = local_context.list_exclusions()
    asset = local_context.get_asset(context["assets"][0]["asset_id"])
    neighbors = local_context.get_neighbors(context["assets"][0]["asset_id"])
    after_queries = conn.execute("SELECT COUNT(*) AS total FROM local_context_queries").fetchone()["total"]

    assert context["ok"] is True
    assert context["evidence_refs"]
    assert any(row["root_path"] == api.norm_path(str(root)) for row in roots)
    assert exclusions == []
    assert asset["ok"] is True
    assert neighbors["ok"] is True
    assert after_queries == before_queries


def test_context_query_uses_entity_match_when_chunk_text_does_not_repeat_entity(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    note = root / "operation.txt"
    note.write_text("SKU scraper productupload queue with ScrapingBee credit guard.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
    asset = conn.execute("SELECT asset_id FROM local_assets WHERE path=?", (str(note),)).fetchone()
    version = conn.execute("SELECT version_id FROM local_asset_versions WHERE asset_id=?", (asset["asset_id"],)).fetchone()
    conn.execute(
        """
        INSERT OR IGNORE INTO local_entities(entity_id, asset_id, version_id, name, entity_type, confidence, evidence, created_at)
        VALUES (?, ?, ?, 'Leebmann24', 'entity', 0.95, 'project alias', 1)
        """,
        (api.stable_id("entity", "leebmann24"), asset["asset_id"], version["version_id"]),
    )
    conn.commit()

    context = local_context.context_query("Leebmann24", limit=5)

    assert context["assets"]
    assert context["assets"][0]["asset_id"] == asset["asset_id"]
    assert context["chunks"][0]["asset_id"] == asset["asset_id"]
    assert context["entities"][0]["name"] == "Leebmann24"


def test_context_query_entity_boost_prefers_matching_chunk_inside_long_asset(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    long_doc = root / "long.txt"
    long_doc.write_text(
        "\n".join([f"Chunk irrelevante {index} sobre calendario y reglas internas." for index in range(40)])
        + "\nLeebmann24 productupload usa ScrapingBee credit guard y cola de SKUs.",
        encoding="utf-8",
    )

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    context = local_context.context_query("Leebmann24 productupload", limit=5)

    assert context["chunks"]
    assert "Leebmann24" in context["chunks"][0]["text"]
    assert "productupload" in context["chunks"][0]["text"]


def test_context_query_entity_assets_are_not_lost_behind_recent_chunk_window(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    old = root / "leebmann-old.txt"
    old.write_text("Leebmann24 SKU scraper productupload con cola pendiente.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
    asset = conn.execute("SELECT asset_id, root_id FROM local_assets WHERE path=?", (str(old),)).fetchone()
    conn.execute("UPDATE local_chunks SET created_at=1 WHERE asset_id=?", (asset["asset_id"],))

    noise_asset_id = "asset_noise_recent"
    noise_version_id = "ver_noise_recent"
    noise_path = str(root / "noise.txt")
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, file_type, extension,
          size_bytes, quick_fingerprint, depth, depth_reason, phase, status, privacy_class, permission_state,
          first_seen_at, last_seen_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '/', 'document', '.txt', 1, 'noise', 2, 'default', 'embeddings', 'active', 'normal', 'granted', 1, 1, 1)
        """,
        (noise_asset_id, asset["root_id"], noise_path, noise_path, str(root)),
    )
    conn.execute(
        "INSERT INTO local_asset_versions(version_id, asset_id, quick_fingerprint, content_hash, size_bytes, modified_at_fs, summary, created_at) VALUES (?, ?, 'noise', '', 1, 1, '', 1)",
        (noise_version_id, noise_asset_id),
    )
    for index in range(5005):
        conn.execute(
            "INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) VALUES (?, ?, ?, ?, 'rules calendar evolution generic noise', 5, ?)",
            (f"noise_chunk_{index}", noise_asset_id, noise_version_id, index, 10_000 + index),
        )
    conn.commit()

    context = local_context.context_query("Leebmann24 productupload", limit=5)

    assert context["assets"]
    assert context["assets"][0]["asset_id"] == asset["asset_id"]
    assert "Leebmann24" in context["chunks"][0]["text"]


def test_status_reports_elapsed_and_eta_for_active_index(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"doc-{index}.txt").write_text(f"Documento {index} sobre Maria y BMW.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=1)
    conn = get_local_context_db()
    conn.execute(
        "INSERT OR REPLACE INTO local_index_state(key, value, updated_at) VALUES (?, ?, ?)",
        (api.INITIAL_INDEX_STARTED_AT_KEY, "1000", 1000),
    )
    conn.commit()
    monkeypatch.setattr(api, "now", lambda: 1600)

    status = local_context.status()

    assert status["global"]["elapsed_seconds"] == 600
    assert status["global"]["files_processed"] >= 1
    assert status["global"]["changes_pending"] >= 1
    assert status["global"]["eta_seconds"] and status["global"]["eta_seconds"] > 0


def test_initial_scan_status_separates_first_pass_from_live_reconcile(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"doc-{index}.txt").write_text(f"Documento {index} sobre Maria.", encoding="utf-8")

    local_context.add_root(str(root))
    first = local_context.run_once(limit=1, process_limit=0)

    assert first["initial_scan"]["complete"] is False
    assert first["live"]["skipped"] is True
    status = local_context.status()
    assert status["global"]["phase"] == "initial_indexing"
    assert status["global"]["initial_scan_complete"] is False

    second = local_context.run_once(limit=20, process_limit=0)

    assert second["initial_scan"]["complete"] is True
    status = local_context.status()
    assert status["initial_scan"]["complete"] is True
    assert status["global"]["initial_discovery_complete"] is True
    assert status["global"]["initial_scan_complete"] is False
    assert status["global"]["phase"] == "initial_indexing"

    third = local_context.run_once(limit=20, process_limit=20)

    assert third["initial_index_complete"] is True
    status = local_context.status()
    assert status["global"]["initial_scan_complete"] is True
    assert status["global"]["initial_index_complete"] is True


def test_initial_index_complete_does_not_regress_during_later_partial_scans(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"note-{index}.txt").write_text(f"Documento posterior {index}.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    assert local_context.status()["global"]["initial_scan_complete"] is True

    later = local_context.run_once(limit=1, process_limit=0)

    assert later["scan"]["partial"] is True
    status = local_context.status()
    assert status["global"]["initial_scan_complete"] is True
    assert status["global"]["index_mode"] == "watching_changes"


def test_clear_index_resets_initial_index_state(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("Documento inicial.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    assert local_context.status()["global"]["initial_scan_complete"] is True

    result = local_context.clear_index()

    assert result["ok"] is True
    status = local_context.status()
    assert status["global"]["files_found"] == 0
    assert status["global"]["initial_scan_complete"] is False
    assert status["global"]["phase"] == "initial_indexing"
    assert float(status["global"]["index_started_at"]) > 0
    assert status["global"]["elapsed_seconds"] >= 0


def test_clear_index_removes_stale_checkpoints(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"note-{index}.txt").write_text(f"Documento {index}.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=1, process_limit=0)
    conn = get_local_context_db()
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_checkpoints").fetchone()["total"] >= 1

    local_context.clear_index()
    local_context.run_once(limit=20, process_limit=0)

    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_checkpoints").fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE status='active'").fetchone()["total"] == 3


def test_index_elapsed_time_uses_persistent_start_state(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("Documento con tiempo estable.", encoding="utf-8")

    local_context.add_root(str(root))
    conn = get_local_context_db()
    conn.execute(
        "UPDATE local_index_state SET value=? WHERE key=?",
        ("1000", api.INITIAL_INDEX_STARTED_AT_KEY),
    )
    conn.commit()

    status = local_context.status()

    assert status["global"]["index_started_at"] == "1000"
    assert status["global"]["elapsed_seconds"] > 1000


def test_context_query_compact_mode_limits_payload_and_explains_parameters(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(4):
        (root / f"maria-{index}.txt").write_text(
            "Maria Riera presupuesto factura proyecto " + ("detalle operativo " * 80),
            encoding="utf-8",
        )

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    context = local_context.context_query(
        "Maria Riera presupuesto factura",
        limit=8,
        mode="compact",
        max_chars=1800,
        include_entities=False,
        include_relations=False,
    )

    assert context["mode"] == "compact"
    assert context["truncated"] is True
    assert context["usage_hint"]["recommended_call"].startswith("nexo_local_context")
    assert context["usage_hint"]["current_params"]["max_chars"] == 1800
    assert context["entities"] == []
    assert context["relations"] == []
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 1800


def test_context_query_enforces_tiny_payload_limit_and_normalizes_mode(tmp_path):
    root = tmp_path / ("docs-" + ("x" * 120))
    root.mkdir()
    (root / ("maria-" + ("y" * 120) + ".txt")).write_text(
        "Maria Riera presupuesto factura " + ("detalle " * 200),
        encoding="utf-8",
    )

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    context = local_context.context_query(
        "Maria Riera " + ("consulta " * 80),
        limit=8,
        mode="garbage",
        max_chars=260,
        include_entities=True,
        include_relations=True,
    )

    assert context["mode"] == "compact"
    assert context["truncated"] is True
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 260


def test_context_router_is_compact_and_does_not_duplicate_result_payload(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(5):
        (root / f"maria-router-{index}.txt").write_text("Maria Riera aceptó presupuesto " + ("detalle " * 80), encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    routed = local_context.context_router("Maria Riera presupuesto", limit=8, max_chars=1000)

    assert "result" not in routed
    assert routed["should_inject"] is True
    assert len(json.dumps(routed, ensure_ascii=False, separators=(",", ":"))) <= 1000


def test_context_router_returns_injectable_compact_evidence(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "maria-router.txt"
    note.write_text("Maria Riera aceptó el presupuesto y pagó la factura.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    routed = local_context.context_router("que sabes de Maria Riera presupuesto", limit=4, max_chars=3000)

    assert routed["ok"] is True
    assert routed["should_inject"] is True
    assert "LOCAL CONTEXT EVIDENCE" in routed["rendered"]
    assert "maria-router.txt" in routed["rendered"]


def test_sensitive_files_are_not_indexed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = root / ".env"
    secret.write_text("OPENAI_API_KEY=secret", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(secret),)).fetchone()
    assert row["total"] == 0
    chunks = conn.execute("SELECT COUNT(*) AS total FROM local_chunks").fetchone()["total"]
    assert chunks == 0


def test_google_maps_key_param_is_inventory_only_not_queryable(tmp_path):
    from local_context.extractors import contains_secret

    root = tmp_path / "web"
    root.mkdir()
    html = root / "map.html"
    html.write_text(
        '<script src="https://maps.googleapis.com/maps/api/js?key=AIzaSyA1234567890123456789012345678901234"></script>',
        encoding="utf-8",
    )

    assert contains_secret(html.read_text(encoding="utf-8")) is True

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    conn = get_local_context_db()
    asset = conn.execute("SELECT privacy_class, phase FROM local_assets WHERE path=?", (str(html),)).fetchone()
    chunks = conn.execute("SELECT COUNT(*) AS total FROM local_chunks WHERE asset_id IN (SELECT asset_id FROM local_assets WHERE path=?)", (str(html),)).fetchone()
    assert asset["privacy_class"] == "content_secret_inventory_only"
    assert chunks["total"] == 0


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

    conn = get_local_context_db()
    for path in files:
        row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(path),)).fetchone()
        assert row["total"] == 0
    indexed = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(normal),)).fetchone()
    assert indexed["total"] == 1


def test_windows_style_private_paths_are_blocked():
    from local_context.privacy import should_extract, should_skip_file, should_skip_tree

    assert should_skip_file(r"C:\Users\me\.boto\credentials") is True
    assert should_skip_file(r"C:\Users\me\AppData\Roaming\npm\.npmrc") is True
    assert should_skip_tree(r"C:\Users\me\.nexo\data") is True
    assert should_skip_file(r"C:\Users\me\AppData\Roaming\Microsoft\Outlook\client.msg") is False
    assert should_extract(r"C:\Users\me\AppData\Roaming\Microsoft\Outlook\client.msg", 2) is True
    assert should_skip_file(r"C:\Users\me\Documents\Outlook Files\archive.pst") is False
    assert should_extract(r"C:\Users\me\Documents\Outlook Files\archive.pst", 2) is False
    assert should_skip_tree(r"C:\Users\me\AppData\Local\Packages\microsoft.windowscommunicationsapps_8wekyb3d8bbwe\LocalState") is False
    assert should_skip_file(r"C:\Users\me\AppData\Local\Packages\microsoft.windowscommunicationsapps_8wekyb3d8bbwe\LocalState\mail.eml") is False
    assert should_extract(r"C:\Users\me\AppData\Local\Packages\microsoft.windowscommunicationsapps_8wekyb3d8bbwe\LocalState\mail.eml", 2) is True
    assert should_skip_tree("/Users/me/Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile") is False
    assert should_skip_file("/Users/me/Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile/message.msg") is False
    assert should_skip_file("/Users/me/Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile/message.olk15Message") is False
    assert should_extract("/Users/me/Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile/message.olk15Message", 2) is False


def test_default_roots_include_local_email_sources_and_extract_messages(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mail_messages = home / "Library" / "Mail" / "V10" / "Account" / "INBOX.mbox" / "Data" / "Messages"
    nexo_email_dir = home / ".nexo" / "runtime" / "nexo-email"
    mail_messages.mkdir(parents=True)
    nexo_email_dir.mkdir(parents=True)

    raw_message = (
        b"Subject: Pedido Leebmann24\r\n"
        b"From: Maria Riera <maria@example.test>\r\n"
        b"To: francisco@example.test\r\n"
        b"\r\n"
        b"El pedido Leebmann24 quedo aceptado y pagado."
    )
    (mail_messages / "1.emlx").write_bytes(str(len(raw_message)).encode("ascii") + b"\n" + raw_message + b"\n<?xml version=\"1.0\"?>")

    email_db = nexo_email_dir / "nexo-email.db"
    import sqlite3

    conn = sqlite3.connect(email_db)
    conn.execute(
        """
        CREATE TABLE sent_email_events(
          sender TEXT, to_addrs TEXT, cc_addrs TEXT, subject TEXT, sent_at TEXT, status TEXT, body_text TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO sent_email_events VALUES ('nexo@example.test', 'maria@example.test', '', 'Presupuesto Leebmann24', '2026-05-12', 'sent', 'Adjunto presupuesto aceptado de Leebmann24')"
    )
    conn.commit()
    conn.close()

    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("local_context.privacy.Path.home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])

    roots = api.ensure_default_roots()
    root_paths = {row["root_path"] for row in roots["roots"]}

    assert api.norm_path(str(home / "Library" / "Mail")) in root_paths
    assert api.norm_path(str(nexo_email_dir)) in root_paths
    mail_root = next(row for row in roots["roots"] if row["root_path"] == api.norm_path(str(home / "Library" / "Mail")))
    assert int(mail_root["depth"]) >= 8

    local_context.run_once(limit=100, process_limit=100)
    context = local_context.context_query("Maria Leebmann24 presupuesto", limit=5)

    assert any("1.emlx" in asset["display_path"] or "nexo-email.db" in asset["display_path"] for asset in context["assets"])
    assert any("Leebmann24" in chunk["text"] for chunk in context["chunks"])


def test_existing_default_root_depth_is_upgraded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])

    local_context.add_root(str(home), depth=2)
    result = api.ensure_default_roots()

    root = next(row for row in result["roots"] if row["root_path"] == api.norm_path(str(home)))
    assert result["updated"] == 1
    assert int(root["depth"]) >= 8


def test_deleted_file_becomes_tombstone(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("hello local context", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    path.unlink()
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
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
    conn = get_local_context_db()
    row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(path),)).fetchone()
    assert row["total"] == 0


def test_file_type_rules_allow_user_include_and_exclude_overrides(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    custom = root / "case.asd"
    note = root / "note.txt"
    custom.write_text("custom format Maria context", encoding="utf-8")
    note.write_text("normal note Maria context", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(custom),)).fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(note),)).fetchone()["total"] == 1

    include = local_context.set_file_type_rule(".asd", action="extract")
    assert include["ok"] is True
    local_context.run_once(limit=20, process_limit=20)
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(custom),)).fetchone()["total"] == 1
    assert conn.execute("SELECT COUNT(*) AS total FROM local_chunks c JOIN local_assets a ON a.asset_id=c.asset_id WHERE a.path=?", (str(custom),)).fetchone()["total"] >= 1

    exclude = local_context.set_file_type_rule(".txt", action="ignore")
    assert exclude["cleanup"]["assets"] >= 1
    local_context.reconcile_live_changes(asset_limit=20, dir_limit=20, file_limit=20)
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(note),)).fetchone()["total"] == 0


def test_file_type_rule_write_retries_when_sidecar_db_is_busy(monkeypatch):
    calls = {"count": 0}
    real_conn = api._conn

    def flaky_conn():
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_conn()

    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(api, "_conn", flaky_conn)

    result = local_context.set_file_type_rule(".retry", action="extract")

    assert result["ok"] is True
    assert result["extension"] == ".retry"
    assert calls["count"] >= 2


def test_roots_seed_v2_migration_removes_legacy_disk_root_but_keeps_user_content(tmp_path):
    disk = tmp_path / "disk"
    home = disk / "Users" / "me"
    system = disk / "Shared"
    home.mkdir(parents=True)
    system.mkdir(parents=True)
    keep_doc = home / "invoice.pdf"
    system_noise = system / "cache.txt"
    keep_doc.write_text("Maria invoice", encoding="utf-8")
    system_noise.write_text("system noise", encoding="utf-8")

    local_context.add_root(str(disk), depth=api.DEFAULT_SYSTEM_ROOT_DEPTH, source="legacy")
    local_context.add_root(str(home), depth=api.DEFAULT_ROOT_DEPTH, source="core_default")
    local_context.run_once(limit=100, process_limit=0)

    plan = local_context.migrate_roots_seed_v2(dry_run=True)
    assert plan["dry_run"] is True
    assert str(disk) in plan["legacy_disk_roots"]
    assert plan["assets_to_purge"] >= 1

    applied = local_context.migrate_roots_seed_v2(dry_run=False)
    assert applied["dry_run"] is False
    conn = get_local_context_db()
    roots = {row["root_path"]: row["status"] for row in conn.execute("SELECT root_path, status FROM local_index_roots").fetchall()}
    assert roots[api.norm_path(str(disk))] == "removed"
    assert roots[api.norm_path(str(home))] == "active"
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(keep_doc),)).fetchone()["total"] == 1
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(system_noise),)).fetchone()["total"] == 0


def test_roots_seed_v2_large_db_archives_rebuilds_and_preserves_config(tmp_path, monkeypatch):
    disk = tmp_path / "disk"
    home = disk / "Users" / "me"
    external = tmp_path / "external"
    ignored = disk / "SystemCache"
    home.mkdir(parents=True)
    external.mkdir()
    ignored.mkdir()
    (home / "invoice.pdf").write_text("Maria invoice", encoding="utf-8")
    (ignored / "cache.txt").write_text("system cache", encoding="utf-8")
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_local_email_roots", lambda: [])
    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)

    local_context.add_root(str(disk), depth=api.DEFAULT_SYSTEM_ROOT_DEPTH, source="legacy")
    local_context.add_root(str(home), depth=api.DEFAULT_ROOT_DEPTH, source="core_default")
    local_context.add_root(str(external), depth=api.DEFAULT_ROOT_DEPTH)
    local_context.add_exclusion(str(ignored), reason="user")
    local_context.set_file_type_rule(".asd", action="extract")
    local_context.run_once(limit=100, process_limit=0)

    db_path = local_context_db_path()
    assert db_path.is_file()
    monkeypatch.setattr(api, "LOCAL_CONTEXT_REBUILD_THRESHOLD_BYTES", 1)

    applied = local_context.migrate_roots_seed_v2(dry_run=False)

    assert applied["ok"] is True
    assert applied["strategy"] == "archive_rebuild"
    assert Path(applied["cleanup"]["backup_dir"]).is_dir()
    assert any(Path(row["backup_path"]).name == db_path.name for row in applied["cleanup"]["moved"])
    conn = get_local_context_db()
    active_roots = {row["root_path"] for row in conn.execute("SELECT root_path FROM local_index_roots WHERE status='active'").fetchall()}
    assert api.norm_path(str(home)) in active_roots
    assert api.norm_path(str(external)) in active_roots
    assert api.norm_path(str(disk)) not in active_roots
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets").fetchone()["total"] == 0
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_exclusions WHERE path=?", (api.norm_path(str(ignored)),)).fetchone()["total"] == 1
    assert conn.execute("SELECT action FROM local_index_file_type_rules WHERE extension='.asd' AND source='user'").fetchone()["action"] == "extract"
    assert conn.execute("SELECT value FROM local_index_state WHERE key=?", (api.ROOT_SEED_VERSION_KEY,)).fetchone()["value"] == str(api.DEFAULT_ROOT_SEED_VERSION)


def test_removed_core_default_root_is_not_reseeded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])
    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)

    first = api.ensure_default_roots()
    assert first["created"] == 1

    removed = local_context.remove_root(str(home))
    assert removed["ok"] is True
    second = api.ensure_default_roots()

    assert second["skipped_removed"] == 1
    assert api.list_roots() == []
    conn = get_local_context_db()
    row = conn.execute("SELECT status, source FROM local_index_roots WHERE root_path=?", (api.norm_path(str(home)),)).fetchone()
    assert row["status"] == "removed"


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

    conn = get_local_context_db()
    skipped = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(dependency),)).fetchone()
    indexed = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(source),)).fetchone()
    assert skipped["total"] == 0
    assert indexed["total"] == 1


def test_user_include_overrides_default_skipped_tree_under_core_root(tmp_path):
    root = tmp_path / "project"
    skipped_tree = root / ".venv"
    dependency = skipped_tree / "lib" / "package.py"
    source = root / "src" / "app.py"
    dependency.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    dependency.write_text("print('dependency override')", encoding="utf-8")
    source.write_text("print('source')", encoding="utf-8")

    local_context.add_root(str(root), source="core_default")
    local_context.run_once(limit=50, process_limit=0)
    conn = get_local_context_db()
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(dependency),)).fetchone()["total"] == 0

    included = local_context.add_root(str(skipped_tree))
    assert included["ok"] is True
    assert included["explicit_override"] is True
    assert included.get("already_included") is not True

    local_context.run_once(limit=50, process_limit=20)
    assert conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(dependency),)).fetchone()["total"] == 1
    asset = conn.execute("SELECT privacy_class, depth_reason FROM local_assets WHERE path=?", (str(dependency),)).fetchone()
    assert asset["privacy_class"] == "normal"
    assert asset["depth_reason"] == "explicit_user_include"

    hygiene = local_context.local_index_hygiene(fix=True)
    assert api.norm_path(str(skipped_tree)) not in hygiene["removed_roots"]
    assert conn.execute("SELECT status FROM local_index_roots WHERE root_path=?", (api.norm_path(str(skipped_tree)),)).fetchone()["status"] == "active"


def test_nexo_product_artifacts_are_skipped_by_default(tmp_path):
    root = tmp_path / "Applications"
    product_artifact = root / "NEXO Desktop QA backups" / "qa-20260509" / "NEXO Desktop QA.app" / "Contents" / "Resources" / "brain-bundle" / "bin" / "postinstall.js"
    normal_doc = root / "client-note.md"
    product_artifact.parent.mkdir(parents=True)
    product_artifact.write_text("internal NEXO bundled artifact Leebmann24", encoding="utf-8")
    normal_doc.write_text("real user note Leebmann24", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=50, process_limit=50)

    conn = get_local_context_db()
    skipped = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(product_artifact),)).fetchone()
    indexed = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(normal_doc),)).fetchone()
    assert skipped["total"] == 0
    assert indexed["total"] == 1


def test_default_roots_add_new_mounted_volumes_incrementally(tmp_path, monkeypatch):
    home = tmp_path / "home"
    external = tmp_path / "ExternalDrive"
    home.mkdir()
    external.mkdir()

    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])

    first = api.ensure_default_roots()
    assert first["created"] == 1

    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [str(external)])
    second = api.ensure_default_roots()

    roots = {row["root_path"] for row in api.list_roots()}
    assert second["created"] == 0
    assert api.norm_path(str(home)) in roots
    assert api.norm_path(str(external)) not in roots

    manual = local_context.add_root(str(external))
    assert manual["ok"] is True
    assert api.norm_path(str(external)) in {row["root_path"] for row in api.list_roots()}


def test_default_roots_start_from_system_volume_and_keep_special_email_roots(tmp_path, monkeypatch):
    startup = tmp_path / "startup"
    home = startup / "Users" / "me"
    mail = home / "Library" / "Mail"
    nexo_email = home / ".nexo" / "runtime" / "nexo-email"
    mounted = tmp_path / "NetworkShare"
    for path in (startup, home, mail, nexo_email, mounted):
        path.mkdir(parents=True)

    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("local_context.privacy.Path.home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [str(startup)])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [str(mounted)])

    result = api.ensure_default_roots()
    roots = {row["root_path"] for row in result["roots"]}

    assert api.norm_path(str(startup)) not in roots
    assert api.norm_path(str(mounted)) not in roots
    assert api.norm_path(str(home)) in roots
    assert api.norm_path(str(nexo_email)) in roots
    assert api.norm_path(str(mail)) in roots


def test_configured_default_roots_are_additive_not_global_override(tmp_path, monkeypatch):
    startup = tmp_path / "startup"
    configured = tmp_path / "selected"
    mounted = tmp_path / "ExternalDisk"
    home = startup / "Users" / "me"
    mail = home / "Library" / "Mail"
    for path in (startup, configured, mounted, home, mail):
        path.mkdir(parents=True)

    monkeypatch.setenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", str(configured))
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("local_context.privacy.Path.home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [str(startup)])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [str(mounted)])

    roots = {row["root_path"] for row in api.ensure_default_roots()["roots"]}

    assert api.norm_path(str(startup)) not in roots
    assert api.norm_path(str(mounted)) not in roots
    assert api.norm_path(str(configured)) in roots
    assert api.norm_path(str(mail)) in roots


def test_system_volume_scan_excludes_system_but_reads_shared_app_data(tmp_path):
    startup = tmp_path / "startup"
    system_file = startup / "System" / "Library" / "internal.txt"
    cache_file = startup / "Library" / "Caches" / "noise.txt"
    app_bundle_file = startup / "Applications" / "Accounting.app" / "Contents" / "Resources" / "manual.txt"
    shared_data = startup / "Library" / "Application Support" / "Accounting" / "clientes.txt"
    user_doc = startup / "Users" / "Shared" / "factura.txt"
    for path in (system_file, cache_file, app_bundle_file, shared_data, user_doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Maria Riera factura contabilidad", encoding="utf-8")

    local_context.add_root(str(startup), depth=api.DEFAULT_SYSTEM_ROOT_DEPTH)
    local_context.run_once(limit=200, process_limit=200)

    conn = get_local_context_db()
    for skipped in (system_file, cache_file, app_bundle_file):
        row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(skipped),)).fetchone()
        assert row["total"] == 0
    for indexed in (shared_data, user_doc):
        row = conn.execute("SELECT COUNT(*) AS total FROM local_assets WHERE path=?", (str(indexed),)).fetchone()
        assert row["total"] == 1


def test_nested_home_root_is_not_scanned_twice_when_volume_root_exists(tmp_path):
    startup = tmp_path / "startup"
    home = startup / "Users" / "me"
    home.mkdir(parents=True)
    doc = home / "Documents" / "factura.txt"
    doc.parent.mkdir()
    doc.write_text("Factura Maria", encoding="utf-8")

    local_context.add_root(str(startup), depth=api.DEFAULT_SYSTEM_ROOT_DEPTH)
    local_context.add_root(str(home), depth=api.DEFAULT_ROOT_DEPTH)
    local_context.run_once(limit=200, process_limit=0)

    conn = get_local_context_db()
    row = conn.execute("SELECT root_id FROM local_assets WHERE path=?", (str(doc),)).fetchone()
    startup_root = conn.execute("SELECT id FROM local_index_roots WHERE root_path=?", (api.norm_path(str(startup)),)).fetchone()
    assert row["root_id"] == startup_root["id"]
    status = local_context.status()
    assert status["initial_scan"]["complete"] is True
    assert status["global"]["initial_discovery_complete"] is True


def test_effective_scan_roots_keep_mounted_volumes_with_system_root():
    roots = [
        {"root_path": "/", "status": "active"},
        {"root_path": "/Users/me", "status": "active"},
        {"root_path": "/Volumes/SharedDisk", "status": "active"},
        {"root_path": "/Users/me/Library/Mail", "status": "active"},
    ]

    effective = [row["root_path"] for row in api._effective_scan_roots(roots)]

    assert "/" in effective
    assert "/Volumes/SharedDisk" in effective
    assert "/Users/me/Library/Mail" in effective
    assert "/Users/me" not in effective


def test_windows_drive_roots_detect_nested_paths():
    assert api._is_nested_path(r"C:\Users\me", r"C:\\") is True
    assert api._is_nested_path(r"C:\Users\me\Documents", r"C:\Users\me") is True
    assert api._path_prefix(r"C:\Users\me") == "C:\\Users\\me\\"


def test_reactivated_offline_root_resets_initial_index_state(tmp_path):
    root = tmp_path / "external"
    root.mkdir()
    (root / "first.txt").write_text("Primer archivo", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    assert local_context.status()["global"]["initial_scan_complete"] is True

    conn = get_local_context_db()
    conn.execute("UPDATE local_index_roots SET status='offline' WHERE root_path=?", (api.norm_path(str(root)),))
    conn.commit()
    (root / "second.txt").write_text("Segundo archivo", encoding="utf-8")

    local_context.add_root(str(root))
    status = local_context.status()

    assert status["global"]["initial_scan_complete"] is False
    assert status["global"]["phase"] == "initial_indexing"


def test_system_temporary_paths_are_skipped_from_root_scan(monkeypatch):
    from local_context.privacy import should_skip_tree

    monkeypatch.delenv("NEXO_LOCAL_INDEX_ALLOW_BLOCKED_ROOTS", raising=False)
    assert should_skip_tree("/tmp/nexo-cache") is True
    assert should_skip_tree("/var/folders/zz/cache") is True
    assert should_skip_tree("/private/var/folders/zz/cache") is True


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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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


def test_performance_profile_is_persisted_and_reported():
    api.ensure_ready()
    default_status = local_context.status()
    assert default_status["performance"]["profile"] == "medium"
    assert default_status["global"]["performance_profile"] == "medium"
    assert "label" not in default_status["performance"]
    assert default_status["performance"]["label_key"] == "local_context.performance.medium"
    assert all("label" not in item for item in default_status["performance"]["available_profiles"])

    updated = local_context.set_performance_profile("alto")
    assert updated["ok"] is True
    assert updated["profile"] == "high"
    assert updated["performance"]["warning"] is True

    status = local_context.status()
    assert status["performance"]["profile"] == "high"
    assert status["global"]["performance_profile"] == "high"


def test_set_performance_profile_retries_when_db_is_locked(monkeypatch):
    api.ensure_ready()
    real_conn = api._conn
    calls = {"conn": 0, "sleep": 0}

    def flaky_conn():
        calls["conn"] += 1
        if calls["conn"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_conn()

    monkeypatch.setattr(api, "_conn", flaky_conn)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: calls.__setitem__("sleep", calls["sleep"] + 1))

    updated = local_context.set_performance_profile("medio")

    assert updated["ok"] is True
    assert updated["profile"] == "medium"
    assert calls == {"conn": 2, "sleep": 1}


def test_run_once_uses_persisted_performance_limits_by_default(monkeypatch):
    calls = {}

    def fake_scan_once(*, limit=None):
        calls["scan_limit"] = limit
        return {"ok": True, "seen": 0, "changed": 0, "errors": 0, "partial": False}

    def fake_process_jobs(*, limit=100):
        calls["process_limit"] = limit
        return {"ok": True, "processed": 0, "failed": 0}

    monkeypatch.setenv("NEXO_LOCAL_INDEX_DISABLE_DEFAULT_ROOTS", "1")
    monkeypatch.setattr(api, "local_index_privacy_hygiene", lambda *, fix=False: {"ok": True})
    monkeypatch.setattr(api, "ensure_default_roots", lambda: None)
    monkeypatch.setattr(api, "scan_once", fake_scan_once)
    monkeypatch.setattr(api, "process_jobs", fake_process_jobs)

    local_context.set_performance_profile("low")
    result = local_context.run_once()

    assert result["performance"]["profile"] == "low"
    assert calls["scan_limit"] == 250
    assert calls["process_limit"] == 50


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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
    row = conn.execute("SELECT status FROM local_assets WHERE display_path=?", (str(path),)).fetchone()
    assert row["status"] == "deleted"


def test_full_scan_marks_deleted_file_and_closes_jobs(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("delete me with pending jobs", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = get_local_context_db()
    asset = conn.execute("SELECT asset_id FROM local_assets WHERE path=?", (str(path),)).fetchone()
    assert asset is not None
    assert conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE asset_id=? AND status='pending'", (asset["asset_id"],)).fetchone()["total"] >= 1

    path.unlink()
    local_context.run_once(limit=20, process_limit=0)

    assert conn.execute("SELECT status FROM local_assets WHERE asset_id=?", (asset["asset_id"],)).fetchone()["status"] == "deleted"
    jobs = conn.execute("SELECT status, last_error_code FROM local_index_jobs WHERE asset_id=?", (asset["asset_id"],)).fetchall()
    assert jobs
    assert all(row["status"] == "done" and row["last_error_code"] == "asset_deleted" for row in jobs)


def test_live_reconcile_reindexes_modified_file(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("first version", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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
    conn = get_local_context_db()
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

    conn = get_local_context_db()
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
    api.ensure_ready()
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
    api.ensure_ready()
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
    api.ensure_ready()
    monkeypatch.setattr(api, "system_label", lambda: "windows")
    monkeypatch.setattr(api, "_command_output", lambda args, **kwargs: (0, "Running\n", ""))

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is True
    assert result["service"]["manager"] == "scheduled_task"
    assert result["service"]["task_name"] == "NEXO Local Memory"


def test_status_reports_ready_windows_scheduled_task_as_operational(monkeypatch):
    api.ensure_ready()
    monkeypatch.setattr(api, "system_label", lambda: "windows")
    monkeypatch.setattr(api, "_command_output", lambda args, **kwargs: (0, "Ready\n", ""))
    monkeypatch.setattr(api, "_process_running", lambda pattern: False)

    result = api.status()

    assert result["service"]["installed"] is True
    assert result["service"]["running"] is True
    assert result["service"]["active_process"] is False


def test_status_uses_readonly_local_context_db(monkeypatch):
    api.ensure_ready()
    monkeypatch.setattr(api, "ensure_local_context_db", lambda: (_ for _ in ()).throw(AssertionError("status must not migrate/write")))

    result = api.status()

    assert result["ok"] is True
    assert "global" in result


def test_status_readonly_does_not_create_wal_sidecars(tmp_path, monkeypatch):
    db_path = tmp_path / "clean-wal-local-context.db"
    monkeypatch.setenv("NEXO_LOCAL_CONTEXT_DB", str(db_path))
    api.ensure_ready()
    conn = api.get_local_context_db()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    close_local_context_db()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()

    result = api.status()

    assert result["ok"] is True
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()


def test_status_read_error_is_keyed_not_spanish_copy(monkeypatch):
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {"installed": True, "running": True})
    result = api._status_read_error(RuntimeError("locked"), code="local_context_db_busy")

    assert result["ok"] is False
    problem = result["problems"][0]
    assert problem["user_message"] == ""
    assert problem["message_key"] == "local_context.status_unavailable"
    assert problem["recommended_action"] == ""
    assert problem["recommended_action_key"] == "local_context.retry_automatic"
    assert "healthy" in result["service"]
    assert "state" in result["service"]


def test_status_service_problems_are_keyed_not_language_specific(monkeypatch):
    api.ensure_ready()
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {
        "installed": False,
        "manager": "launchagent",
        "platform": "macos",
    })

    result = api.status()

    problem = result["problems"][0]
    assert problem["support_code"] == "local_index_service_not_installed"
    assert problem["user_message"] == ""
    assert problem["message_key"] == "local_context.problem.service_not_installed"
    assert problem["recommended_action"] == ""
    assert problem["recommended_action_key"] == "local_context.reopen_or_update_desktop"


def test_status_handles_invalid_local_context_db_without_raising(tmp_path, monkeypatch):
    broken = tmp_path / "broken-local-context.db"
    broken.write_text("not sqlite", encoding="utf-8")
    monkeypatch.setenv("NEXO_LOCAL_CONTEXT_DB", str(broken))
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {"installed": True, "running": True})

    result = api.status()

    assert result["ok"] is False
    assert result["error"] == "local_context_db_invalid"
    assert result["global"] is None
    assert result["service"]["healthy"] is True


def test_status_distinguishes_missing_schema_from_busy(tmp_path, monkeypatch):
    import sqlite3 as _sqlite3

    partial = tmp_path / "partial-local-context.db"
    conn = _sqlite3.connect(partial)
    conn.execute("CREATE TABLE unrelated(id INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEXO_LOCAL_CONTEXT_DB", str(partial))
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {"installed": True, "running": True})

    result = api.status()

    assert result["ok"] is False
    assert result["error"] == "local_context_db_schema_missing"


def test_status_rejects_partially_migrated_schema(monkeypatch):
    api.ensure_ready()
    conn = api.get_local_context_db()
    conn.execute("DROP TABLE local_chunks")
    conn.commit()
    close_local_context_db()
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {"installed": True, "running": True})

    result = api.status()

    assert result["ok"] is False
    assert result["error"] == "local_context_db_schema_missing"
    assert result["global"] is None


def test_replace_chunks_persists_active_embedding_profile(monkeypatch):
    conn = get_local_context_db()

    def fake_embed_record(text: str) -> dict:
        return {
            "vector": [0.25, 0.75],
            "model_id": "test-semantic-model",
            "model_revision": "rev-1",
            "dimension": 2,
            "profile": "test-profile",
            "kind": "fastembed_embedding",
        }

    monkeypatch.setattr(api.embeddings, "embed_record", fake_embed_record)

    api._replace_chunks(conn, "asset_profile", "version_profile", "Texto de prueba para embedding real.")
    row = conn.execute("SELECT model_id, model_revision, dimension, vector_json FROM local_embeddings").fetchone()

    assert row["model_id"] == "test-semantic-model"
    assert row["model_revision"] == "rev-1"
    assert row["dimension"] == 2
    assert json.loads(row["vector_json"]) == [0.25, 0.75]


def test_process_jobs_refreshes_stale_hash_embeddings(monkeypatch):
    conn = get_local_context_db()
    profile = api.embeddings.EmbeddingProfile(
        model_id="test-semantic-model",
        model_revision="rev-2",
        dimension=2,
        kind="fastembed_embedding",
        state="available",
        profile="test-profile",
    )
    monkeypatch.setattr(api.embeddings, "active_profile", lambda: profile)
    monkeypatch.setattr(
        api.embeddings,
        "embed_record",
        lambda text: {
            "vector": [1.0, 0.0],
            "model_id": profile.model_id,
            "model_revision": profile.model_revision,
            "dimension": profile.dimension,
            "profile": profile.profile,
            "kind": profile.kind,
        },
    )
    asset_id = "asset_stale_embedding"
    version_id = "version_stale_embedding"
    chunk_id = "chunk_stale_embedding"
    conn.execute(
        """
        INSERT INTO local_assets(asset_id, root_id, path, display_path, parent_path, volume_id, file_type, extension,
          size_bytes, quick_fingerprint, depth, depth_reason, phase, status, privacy_class, permission_state,
          first_seen_at, last_seen_at, updated_at)
        VALUES (?, 1, '/tmp/stale.txt', '/tmp/stale.txt', '/tmp', '/', 'document', '.txt', 1, 'old', 2, 'default',
          'embeddings', 'active', 'normal', 'granted', 1, 1, 1)
        """,
        (asset_id,),
    )
    conn.execute(
        "INSERT INTO local_asset_versions(version_id, asset_id, quick_fingerprint, content_hash, size_bytes, modified_at_fs, summary, created_at) VALUES (?, ?, 'old', '', 1, 1, '', 1)",
        (version_id, asset_id),
    )
    conn.execute(
        "INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) VALUES (?, ?, ?, 0, 'semantic text', 2, 1)",
        (chunk_id, asset_id, version_id),
    )
    conn.execute(
        "INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at) VALUES ('old_embedding', ?, ?, 'nexo-local-hash-embedding', '1', 128, '[0]', 1)",
        (asset_id, chunk_id),
    )
    conn.commit()

    result = api.process_jobs(limit=5)
    row = conn.execute("SELECT model_id, model_revision, dimension, vector_json FROM local_embeddings WHERE asset_id=?", (asset_id,)).fetchone()

    assert result["embedding_refresh_queued"] == 1
    assert result["processed"] == 1
    assert row["model_id"] == profile.model_id
    assert row["model_revision"] == profile.model_revision
    assert row["dimension"] == profile.dimension
    assert json.loads(row["vector_json"]) == [1.0, 0.0]


def test_local_context_reranker_can_reorder_top_candidates(monkeypatch):
    class FakeReranker:
        def rerank(self, query: str, docs: list[str]) -> list[float]:
            assert query == "factura bmw"
            assert docs == ["weak candidate", "strong candidate"]
            return [-3.0, 8.0]

    monkeypatch.setattr(api, "_context_reranker", lambda: FakeReranker())
    scored = [
        (0.9, {"chunk_id": "weak", "text": "weak candidate"}),
        (0.7, {"chunk_id": "strong", "text": "strong candidate"}),
    ]

    result = api._rerank_scored_candidates("factura bmw", scored, limit=2)

    assert [row["chunk_id"] for _score, row in result] == ["strong", "weak"]


def test_model_status_has_local_fallback():
    result = api.model_status()
    assert result["ok"] is True
    assert any(model["kind"] == "deterministic_embedding" and model["state"] == "available" for model in result["models"])


def test_scan_sort_prioritizes_user_documents_before_system_dirs(tmp_path):
    users = tmp_path / "Users"
    applications = tmp_path / "Applications"
    documents = users / "francisco" / "Documents"
    library = users / "francisco" / "Library"
    for directory in (applications, documents, library):
        directory.mkdir(parents=True)
    pdf = documents / "contrato.pdf"
    blob = documents / "z-unknown.bin"
    pdf.write_text("contrato importante", encoding="utf-8")
    blob.write_text("binary-ish", encoding="utf-8")

    root_entries = sorted([applications, users], key=api._scan_entry_sort_key)
    doc_entries = sorted([blob, pdf], key=api._scan_entry_sort_key)

    assert root_entries == [users, applications]
    assert doc_entries == [pdf, blob]


def test_initial_scan_limit_reaches_known_documents_before_system_noise(tmp_path):
    root = tmp_path / "disk"
    contract = root / "Users" / "francisco" / "Documents" / "contrato.pdf"
    noise = root / "Applications" / "RandomApp" / "cache.bin"
    contract.parent.mkdir(parents=True)
    noise.parent.mkdir(parents=True)
    contract.write_text("contrato prioritario", encoding="utf-8")
    noise.write_text("ruido", encoding="utf-8")

    local_context.add_root(str(root))
    result = local_context.run_once(limit=1, process_limit=0)
    conn = get_local_context_db()
    rows = conn.execute("SELECT path FROM local_assets ORDER BY first_seen_at ASC").fetchall()

    assert result["scan"]["seen"] == 1
    assert rows
    assert rows[0]["path"].endswith("contrato.pdf")
