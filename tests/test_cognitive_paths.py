from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _reload(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.delenv("NEXO_COGNITIVE_DB", raising=False)
    import cognitive_paths
    importlib.reload(cognitive_paths)
    return cognitive_paths


def _sqlite_db(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.execute("INSERT INTO marker(value) VALUES (?)", (value,))
    conn.commit()
    conn.close()
    return path


def test_canonical_cognitive_db_path_uses_runtime_cognitive_dir(tmp_path, monkeypatch):
    cognitive_paths = _reload(monkeypatch, tmp_path / "nexo-home")

    assert cognitive_paths.canonical_cognitive_db_path() == tmp_path / "nexo-home" / "runtime" / "cognitive" / "cognitive.db"
    assert cognitive_paths.resolve_cognitive_db(for_write=True) == cognitive_paths.canonical_cognitive_db_path()
    assert cognitive_paths.audit_cognitive_db_paths()["reason"] == "canonical_only"


def test_legacy_runtime_data_db_is_copied_to_canonical_without_deleting_source(tmp_path, monkeypatch):
    cognitive_paths = _reload(monkeypatch, tmp_path / "nexo-home")
    legacy = tmp_path / "nexo-home" / "runtime" / "data" / "cognitive.db"
    _sqlite_db(legacy, "legacy")

    resolved = cognitive_paths.resolve_cognitive_db(for_write=True)

    assert resolved == cognitive_paths.canonical_cognitive_db_path()
    assert resolved.exists()
    assert legacy.exists(), "legacy DB is retained for operator-verifiable rollback"
    assert cognitive_paths.audit_cognitive_db_paths()["reason"] == "legacy_duplicate_retained"
    marker = tmp_path / "nexo-home" / "runtime" / "state" / "cognitive-db-migration.json"
    assert marker.exists()
    assert str(legacy) in marker.read_text(encoding="utf-8")


def test_divergent_canonical_and_legacy_db_blocks_writes(tmp_path, monkeypatch):
    cognitive_paths = _reload(monkeypatch, tmp_path / "nexo-home")
    _sqlite_db(cognitive_paths.canonical_cognitive_db_path(), "canonical")
    _sqlite_db(tmp_path / "nexo-home" / "runtime" / "data" / "cognitive.db", "legacy")

    audit = cognitive_paths.audit_cognitive_db_paths()
    assert audit["status"] == "error"
    assert audit["reason"] == "canonical_and_legacy_diverge"
    with pytest.raises(cognitive_paths.CognitiveDbPathConflict):
        cognitive_paths.resolve_cognitive_db(for_write=True)


def test_env_override_is_respected_without_legacy_migration(tmp_path, monkeypatch):
    override = tmp_path / "override" / "cognitive.db"
    legacy = tmp_path / "nexo-home" / "runtime" / "data" / "cognitive.db"
    _sqlite_db(legacy, "legacy")
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    monkeypatch.setenv("NEXO_COGNITIVE_DB", str(override))

    import cognitive_paths
    importlib.reload(cognitive_paths)

    assert cognitive_paths.resolve_cognitive_db(for_write=True) == override
    assert not override.exists()
    assert cognitive_paths.audit_cognitive_db_paths()["reason"] == "legacy_only"


def test_no_write_callsite_hardcodes_runtime_data_cognitive_db():
    offenders: list[str] = []
    needles = (
        'data_dir() / "cognitive.db"',
        "data_dir() / 'cognitive.db'",
        'paths.data_dir() / "cognitive.db"',
        "paths.data_dir() / 'cognitive.db'",
        'runtime/data/cognitive.db',
    )
    for file_path in SRC.rglob("*.py"):
        if file_path.name == "cognitive_paths.py":
            continue
        text = file_path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            offenders.append(str(file_path.relative_to(ROOT)))
    assert offenders == []
