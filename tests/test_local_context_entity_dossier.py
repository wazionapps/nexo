from __future__ import annotations

from pathlib import Path

import local_context
from local_context.db import get_local_context_db


def _index_twice(root: Path) -> None:
    local_context.add_root(str(root))
    first = local_context.run_once(limit=100, process_limit=100)
    assert first["ok"] is True
    second = local_context.run_once(limit=100, process_limit=100)
    assert second["ok"] is True


def test_entity_dossier_canonicalizes_variants_to_same_entity(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "one.txt").write_text("Max Foster\ntiempo total: 100", encoding="utf-8")
    (root / "two.txt").write_text("Maximilian Foster\ntiempo total: 200", encoding="utf-8")

    _index_twice(root)

    conn = get_local_context_db()
    rows = conn.execute(
        """
        SELECT DISTINCT entity_id
        FROM local_entity_aliases
        WHERE normalized_alias IN ('max foster', 'maximilian foster')
        """
    ).fetchall()
    assert len(rows) == 1

    dossier = local_context.entity_dossier("Max Foster")
    assert dossier["ok"] is True
    assert dossier["needs_disambiguation"] is False
    assert dossier["recall"]["assets_total"] == 2


def test_entity_dossier_extracts_open_claims_from_personal_and_object_notes(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "family-1.txt").write_text(
        "Francisca Madre\ncolor favorito: azul\ntiempo paseo: 45\nfecha visita: 2026-01-10",
        encoding="utf-8",
    )
    (root / "family-2.txt").write_text(
        "Francisca Madre\nplanta preferida: lavanda\ntiempo paseo: 30\nfecha llamada: 2026-02-03",
        encoding="utf-8",
    )
    (root / "object-note.txt").write_text(
        "Bici Roja\npeso aproximado: 14.5\nfecha ajuste: 2026-03-04",
        encoding="utf-8",
    )

    _index_twice(root)

    dossier = local_context.entity_dossier("Francisca Madre", max_chars=50000)
    object_dossier = local_context.entity_dossier("Bici Roja", max_chars=50000)
    assert dossier["ok"] is True
    assert dossier["aggregates"]["documents_total"] == 2
    assert dossier["aggregates"]["numeric_by_predicate"]["tiempo paseo"]["count"] == 2
    assert dossier["aggregates"]["numeric_by_predicate"]["tiempo paseo"]["sum"] == 75.0
    assert dossier["aggregates"]["date_range"]["fact_min"] == "2026-01-10"
    assert dossier["aggregates"]["date_range"]["fact_max"] == "2026-02-03"
    assert dossier["evidence_refs"]
    assert any(fact["predicate"] == "planta preferida" for fact in dossier["facts"])
    assert object_dossier["aggregates"]["numeric_by_predicate"]["peso aproximado"]["max"] == 14.5


def test_entity_dossier_preserves_semantic_context_query_behavior(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "shared-note.txt").write_text("Clara Rivera\nrecuerdo compartido: paseo junto al mar", encoding="utf-8")

    _index_twice(root)

    context = local_context.context_query("recuerdo compartido Clara Rivera", limit=5)
    dossier = local_context.entity_dossier("Clara Rivera")

    assert context["ok"] is True
    assert context["evidence_refs"]
    assert context["mode"] == "full"
    assert dossier["ok"] is True
    assert dossier["mode"] == "entity_dossier"
    assert dossier["evidence_refs"]


def test_entity_dossier_privacy_blocks_secrets_and_excluded_trees(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    ignored = root / "ignored"
    ignored.mkdir()
    (root / "public.txt").write_text("Persona Segura\nestado: visible", encoding="utf-8")
    (root / "secret.txt").write_text("Persona Segura\napi token: Bearer abcdefghijklmnop123456", encoding="utf-8")
    (ignored / "hidden.txt").write_text("Persona Segura\nestado: oculto", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.add_exclusion(str(ignored))
    local_context.run_once(limit=100, process_limit=100)
    local_context.run_once(limit=100, process_limit=100)

    dossier = local_context.entity_dossier("Persona Segura", max_chars=50000)
    paths = " ".join(asset["display_path"] for asset in dossier["assets"])
    values = " ".join(fact["value"] for fact in dossier["facts"])

    assert dossier["ok"] is True
    assert "public.txt" in paths
    assert "secret.txt" not in paths
    assert "hidden.txt" not in paths
    assert "Bearer" not in values
