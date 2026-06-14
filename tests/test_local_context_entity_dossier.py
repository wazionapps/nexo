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


def _heavy_entity_dossier_payload():
    """Payload realista de un proveedor con cientos de facts/chunks (como Banco
    Sabadell en producción), suficiente para desbordar el budget de chars."""
    from local_context import api

    assets = [
        {
            "asset_id": f"asset_{i:03d}",
            "display_path": f"~/Documents/facturas/proveedor_x/factura_{i:03d}.pdf",
            "file_type": "pdf",
            "extension": ".pdf",
            "size_bytes": 12000 + i,
            "created_at_fs": 1700000000 + i * 86400,
            "modified_at_fs": 1700000000 + i * 86400,
            "first_seen_at": 1700000000 + i * 86400,
            "last_seen_at": 1700000000 + i * 86400,
        }
        for i in range(40)
    ]
    facts = [
        {
            "fact_id": f"fact_{i:04d}",
            "entity_id": "entity_proveedor_x",
            "predicate": "importe total" if i % 2 == 0 else "fecha factura",
            "value": f"{1000 + i},{i % 100:02d} EUR — factura del proveedor X, concepto detallado {i}",
            "value_number": float(1000 + i) if i % 2 == 0 else None,
            "value_date": "" if i % 2 == 0 else f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "source_asset_id": f"asset_{i % 40:03d}",
            "source_chunk_id": f"chunk_{i:04d}",
            "confidence": 0.40 + (i % 50) / 100.0,
            "created_at": 1700000000 + i,
        }
        for i in range(400)
    ]
    chunks = [
        {
            "chunk_id": f"chunk_{i:04d}",
            "asset_id": f"asset_{i % 40:03d}",
            "chunk_index": i,
            "text": "Factura del proveedor X. Importe total y fecha de emision. " * 12,
        }
        for i in range(80)
    ]
    aggregates = api._aggregate_dossier(assets, facts)
    evidence_refs = [
        f"local_asset:{f['source_asset_id']}#chunk:{f['source_chunk_id']}" for f in facts[:60]
    ]
    return {
        "ok": True,
        "mode": "entity_dossier",
        "query": "Proveedor X",
        "confidence": 1.0,
        "needs_disambiguation": False,
        "entity": {
            "entity_id": "entity_proveedor_x",
            "display_name": "Proveedor X",
            "entity_type": "entity",
            "score": 1.0,
            "confidence": 0.9,
            "aliases": ["Proveedor X"],
            "asset_count": 40,
        },
        "candidates": [{"entity_id": "entity_proveedor_x", "display_name": "Proveedor X", "score": 1.0}],
        "recall": {
            "assets_total": 40,
            "assets_returned": 40,
            "facts_returned": 400,
            "chunks_returned": 80,
            "hard_caps": {"assets": 500, "facts": 3000, "chunks": 1200},
        },
        "aggregates": aggregates,
        "assets": assets,
        "facts": facts,
        "chunks": chunks,
        "evidence_refs": evidence_refs,
        "warnings": [],
        "llm_presence": {"enabled": False, "available": False},
        "synthesis_contract": {"instruction": "Use only aggregates/facts/evidence_refs.", "evidence_required": True},
    }


def test_entity_dossier_truncation_keeps_facts_and_aggregates_at_production_max_chars():
    """Regresion (G-A): un proveedor pesado NO debe volver vacio al truncar al
    max_chars REAL de produccion (20000). El bug: _truncate_context_payload solo
    recortaba chunks/assets, nunca facts/aggregates, asi que el payload seguia
    desbordando y caia a _minimal_truncated_context_payload (todo vacio, sin el
    oro: importes y fechas agregados)."""
    from local_context import api

    payload = _heavy_entity_dossier_payload()
    # El payload pesado supera de verdad el budget de produccion (si no, el test no prueba nada).
    assert api._payload_size(payload) > 20000

    result = api._truncate_context_payload(payload, max_chars=20000)

    # Cabe en el budget...
    assert api._payload_size(result) <= 20000
    # ...pero NO se vacio al minimal: el dossier sigue siendo util para el LLM.
    assert result.get("mode") == "entity_dossier"
    assert result.get("facts"), "facts no debe quedar vacio tras truncar"
    aggregates = result.get("aggregates") or {}
    assert aggregates.get("documents_total", 0) > 0, "aggregates debe sobrevivir al truncado"
    assert aggregates.get("numeric_by_predicate"), "los importes agregados (el oro) deben sobrevivir"
    assert result.get("evidence_refs"), "evidence_refs (trazabilidad) no debe quedar vacio"
