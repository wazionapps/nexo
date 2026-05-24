from __future__ import annotations

import sqlite3
from pathlib import Path


def _write_backup_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE session_diary (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            created_at TEXT,
            decisions TEXT,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            summary TEXT,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            self_critique TEXT,
            source TEXT,
            quality_tier TEXT,
            quality_score INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO session_diary VALUES (
            2866,
            'nexo-1777671989-6454',
            '2026-05-01 22:17:20',
            'assistant email infrastructure decision',
            '',
            'finish agent email routing',
            'continue with nexo-desktop.com assistant mailbox',
            'Diseño infraestructura email asistente@nexo-desktop.com',
            'neutral',
            'product_public',
            '',
            'self critique',
            'auto-close',
            'agent_authored',
            70
        )
        """
    )
    conn.commit()
    conn.close()


def test_reconcile_backup_diaries_indexes_historical_memory(tmp_path):
    from db import fts_search, get_db
    from memory_fabric import memory_fabric_health, reconcile_backup_diaries

    backup = tmp_path / "runtime" / "backups" / "weekly" / "weekly-2026-W18.db"
    _write_backup_db(backup)

    result = reconcile_backup_diaries(backups_root=backup.parent, limit=100)

    assert result["inserted"] == 1
    conn = get_db()
    row = conn.execute("SELECT * FROM historical_diary_index WHERE session_id=?", ("nexo-1777671989-6454",)).fetchone()
    assert row is not None
    assert row["source_backup_path"] == str(backup)

    hits = fts_search("asistente@nexo-desktop.com", limit=5)
    assert any(hit["source"] == "historical_diary" for hit in hits)

    import knowledge_graph as kg

    assert kg.get_node("session", "session:nexo-1777671989-6454") is not None
    assert kg.get_node("email", "email:asistente@nexo-desktop.com") is not None

    health = memory_fabric_health(include_backup_scan=False)
    assert health["historical_diaries"]["index_rows"] == 1


def test_reconcile_backup_diaries_skips_active_memory(tmp_path):
    from db import get_db
    from memory_fabric import reconcile_backup_diaries

    backup = tmp_path / "runtime" / "backups" / "weekly" / "weekly-2026-W18.db"
    _write_backup_db(backup)
    conn = get_db()
    conn.execute(
        """
        INSERT INTO session_diary (
            session_id, created_at, decisions, discarded, pending, context_next,
            mental_state, summary, domain, user_signals, self_critique, source
        )
        VALUES (?, ?, '', '', '', '', '', ?, 'product_public', '', '', 'test')
        """,
        (
            "nexo-1777671989-6454",
            "2026-05-01 22:17:20",
            "Diseño infraestructura email asistente@nexo-desktop.com",
        ),
    )
    conn.commit()

    result = reconcile_backup_diaries(backups_root=backup.parent, limit=100)

    assert result["inserted"] == 0
    assert result["skipped_active"] == 1


def test_reconcile_backup_diaries_deduplicates_same_diary_across_backups(tmp_path):
    from db import get_db
    from memory_fabric import memory_fabric_health, reconcile_backup_diaries

    root = tmp_path / "runtime" / "backups"
    _write_backup_db(root / "nexo-2026-05-01-2200.db")
    _write_backup_db(root / "weekly" / "weekly-2026-W18.db")

    result = reconcile_backup_diaries(backups_root=root, max_backup_files=10, limit=100)

    assert result["scanned_backups"] == 2
    assert result["inserted"] == 1
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM historical_diary_index").fetchone()[0]
    assert total == 1

    health = memory_fabric_health(include_backup_scan=True, backups_root=root)
    assert health["historical_diaries"]["backup_rows_unreconciled"] == 0
    assert not any(issue["code"] == "backup_diaries_not_reconciled" for issue in health["issues"])


def test_reconcile_backup_diaries_scans_weekly_before_recent_hourlies(tmp_path):
    from db import get_db
    from memory_fabric import reconcile_backup_diaries

    root = tmp_path / "runtime" / "backups"
    for index in range(20):
        _write_backup_db(root / f"nexo-2026-05-02-{index:04d}.db")
    weekly = root / "weekly" / "weekly-2026-W18.db"
    _write_backup_db(weekly)

    result = reconcile_backup_diaries(backups_root=root, max_backup_files=5, limit=100)

    assert result["scanned_backups"] == 5
    conn = get_db()
    row = conn.execute("SELECT source_backup_path FROM historical_diary_index").fetchone()
    assert row["source_backup_path"] == str(weekly)


def test_fts_search_tokenizes_email_like_queries():
    from db import fts_search, fts_upsert

    fts_upsert(
        "historical_diary",
        "email-token-test",
        "Assistant mailbox",
        "Routing decision for asistente@nexo-desktop.com and support/nexo:desktop references.",
        "product_public",
    )

    hits = fts_search("asistente@nexo-desktop.com", limit=5)

    assert any(hit["source_id"] == "email-token-test" for hit in hits)
