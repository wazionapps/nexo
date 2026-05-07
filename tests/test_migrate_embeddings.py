from __future__ import annotations

import importlib
import sys


def test_base_embedding_dim_falls_back_when_manifest_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "home"))
    sys.modules.pop("migrate_embeddings", None)
    migrate_embeddings = importlib.import_module("migrate_embeddings")

    def missing_manifest(_name: str):
        raise FileNotFoundError("missing manifest")

    monkeypatch.setattr(migrate_embeddings, "get_local_model_spec", missing_manifest)

    assert migrate_embeddings._base_embedding_dim() == 384
