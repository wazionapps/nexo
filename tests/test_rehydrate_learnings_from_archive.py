"""Tests for the markdown learnings rehydration helper."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
SCRIPT_PATH = REPO_ROOT / "src" / "scripts" / "rehydrate_learnings_from_archive.py"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_module(monkeypatch, tmp_path):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    spec = importlib.util.spec_from_file_location("rehydrate_learnings_from_archive", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, home


def test_parse_archive_dir_extracts_table_rows_and_bullets(tmp_path, monkeypatch):
    module, _ = _load_module(monkeypatch, tmp_path)

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "nexo-ops.md").write_text(
        """# Errores y Soluciones — NEXO Operativo

## NEXO — Errores Operativos
| Error | Solucion |
|-------|----------|
| Verificar plugins actualizados comparando contra marketplace LOCAL que puede estar stale | SIEMPRE verificar contra GitHub upstream |
| ~~RECURRENTE: No usar LLM externo para tareas mecánicas~~ | OBSOLETO — LLM externo eliminado del stack |

## 2026-02-26 — Errores operativos NEXO
- **Error 1: Puerto SSH incorrecto.** Probé puertos 22 y 22022 antes de leer credentials.md.
- **Regla:** Rutas locales IDE: `example-shop-sys` = servidor ExampleStore.
""",
        encoding="utf-8",
    )

    records = module.parse_archive_dir(archive_dir)

    assert len(records) == 4
    titles = {item.title for item in records}
    assert "Verificar plugins actualizados comparando contra marketplace LOCAL que puede estar stale" in titles
    assert any(item.status == "superseded" for item in records)
    assert any("Puerto SSH incorrecto" in item.title for item in records)
    assert any(item.prevention.startswith("Regla:") for item in records)


def test_apply_candidates_inserts_only_missing_rows(tmp_path, monkeypatch):
    module, home = _load_module(monkeypatch, tmp_path)

    module.init_db()
    conn = module.get_db()
    conn.execute(
        "INSERT INTO learnings (category, title, content, created_at, updated_at) VALUES (?, ?, ?, 1, 1)",
        ("nexo-ops", "Error repetido", "ya existe"),
    )
    conn.commit()

    candidates = [
        module.LearningCandidate("nexo-ops", "Error repetido", "ya existe", "source", "ya existe"),
        module.LearningCandidate("nexo-ops", "Nuevo aprendizaje", "contenido", "source", "SIEMPRE probar"),
    ]

    summary = module.apply_candidates(candidates, apply=True)

    assert summary["parsed"] == 2
    assert summary["inserted"] == 1
    assert summary["skipped_existing"] == 1
