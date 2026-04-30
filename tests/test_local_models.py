from __future__ import annotations

import hashlib
import json
from pathlib import Path

import local_models


def _make_file(root: Path, relative_path: str, content: bytes) -> tuple[int, str]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return len(content), hashlib.sha256(content).hexdigest()


def test_manifest_declares_revision_for_all_pinned_fastembed_models():
    specs = {spec.name: spec for spec in local_models.list_local_model_specs()}
    assert set(specs) == {
        "bge-base-embeddings",
        "bge-small-embeddings",
        "cross-encoder-reranker",
    }
    for spec in specs.values():
        assert len(spec.revision) == 40
        assert spec.source_repo
        assert spec.required_files


def test_list_local_model_specs_degrades_to_empty_when_manifest_is_missing(tmp_path, monkeypatch):
    local_models._load_manifest.cache_clear()
    missing_manifest = tmp_path / "local_model_manifest.json"
    monkeypatch.setattr(local_models, "MANIFEST_PATH", missing_manifest)

    try:
        assert local_models.list_local_model_specs() == []
    finally:
        local_models._load_manifest.cache_clear()


def test_verify_local_model_dir_detects_checksum_drift(tmp_path):
    good_size, good_sha = _make_file(tmp_path, "model.onnx", b"good-model")
    spec = local_models.LocalModelSpec(
        name="fake-model",
        kind="fastembed_embedding",
        model_id="fake/model",
        source_repo="fake/model",
        revision="1234567890abcdef1234567890abcdef12345678",
        model_file="model.onnx",
        source="tests",
        required_files=(
            local_models.LocalModelFile(path="model.onnx", size=good_size, sha256=good_sha),
        ),
    )
    assert local_models.verify_local_model_dir(spec, tmp_path)["ok"] is True
    (tmp_path / "model.onnx").write_bytes(b"bad-model")
    result = local_models.verify_local_model_dir(spec, tmp_path)
    assert result["ok"] is False
    assert any(
        "size mismatch:model.onnx" in item or "sha256 mismatch:model.onnx" in item
        for item in result["problems"]
    )


def test_ensure_local_model_materializes_pinned_snapshot(tmp_path, monkeypatch):
    snapshot_dir = tmp_path / "snapshot"
    size, sha = _make_file(snapshot_dir, "onnx/model.onnx", b"reranker")
    _make_file(snapshot_dir, "config.json", b"{}")
    _make_file(snapshot_dir, "tokenizer.json", b"{\"a\":1}")

    config_size = (snapshot_dir / "config.json").stat().st_size
    config_sha = hashlib.sha256((snapshot_dir / "config.json").read_bytes()).hexdigest()
    tok_size = (snapshot_dir / "tokenizer.json").stat().st_size
    tok_sha = hashlib.sha256((snapshot_dir / "tokenizer.json").read_bytes()).hexdigest()

    spec = local_models.LocalModelSpec(
        name="fake-reranker",
        kind="fastembed_reranker",
        model_id="fake/reranker",
        source_repo="fake/reranker",
        revision="abcdefabcdefabcdefabcdefabcdefabcdefabcd",
        model_file="onnx/model.onnx",
        source="tests",
        required_files=(
            local_models.LocalModelFile(path="config.json", size=config_size, sha256=config_sha),
            local_models.LocalModelFile(path="tokenizer.json", size=tok_size, sha256=tok_sha),
            local_models.LocalModelFile(path="onnx/model.onnx", size=size, sha256=sha),
        ),
    )

    monkeypatch.setattr(local_models, "get_local_model_spec", lambda name: spec)
    monkeypatch.setattr(local_models, "models_dir", lambda: tmp_path / "runtime" / "models")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kwargs: str(snapshot_dir))

    target = local_models.ensure_local_model("fake-reranker")
    assert (target / "onnx" / "model.onnx").read_bytes() == b"reranker"
    lock = json.loads((target / local_models.MODEL_LOCK_FILENAME).read_text(encoding="utf-8"))
    assert lock["revision"] == spec.revision
    assert lock["source_repo"] == spec.source_repo
    assert local_models.verify_local_model_dir(spec, target)["ok"] is True
