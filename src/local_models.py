"""Pinned local model management for Brain embeddings + reranker.

FastEmbed's built-in registry resolves supported models by friendly name, but
its downloader tracks the current upstream repo head unless the caller builds a
stronger contract around it. This module provides that stronger contract:

- source repo pinned by immutable revision SHA
- required files + sha256 checksums stored in-repo
- deterministic materialization under ``~/.nexo/runtime/models``
- FastEmbed instantiated via ``specific_model_path`` so runtime loads the exact
  downloaded artifacts instead of following a floating registry download
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import paths


MANIFEST_PATH = Path(__file__).resolve().with_name("local_model_manifest.json")
MODEL_LOCK_FILENAME = ".nexo-model-lock.json"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LocalModelSpec:
    name: str
    kind: str
    model_id: str
    source_repo: str
    revision: str
    model_file: str
    source: str
    required_files: tuple[LocalModelFile, ...]


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_payload(spec: LocalModelSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "kind": spec.kind,
        "model_id": spec.model_id,
        "source_repo": spec.source_repo,
        "revision": spec.revision,
        "model_file": spec.model_file,
        "required_files": [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in spec.required_files
        ],
    }


@lru_cache(maxsize=1)
def _load_manifest() -> dict[str, LocalModelSpec]:
    if not MANIFEST_PATH.exists():
        logger.warning("local_model_manifest.json missing — running with empty manifest")
        return {}
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    specs: dict[str, LocalModelSpec] = {}
    for raw in payload.get("models", []) or []:
        files = tuple(LocalModelFile(**item) for item in raw.get("required_files", []) or [])
        spec = LocalModelSpec(
            name=str(raw["name"]),
            kind=str(raw["kind"]),
            model_id=str(raw["model_id"]),
            source_repo=str(raw["source_repo"]),
            revision=str(raw["revision"]),
            model_file=str(raw["model_file"]),
            source=str(raw["source"]),
            required_files=files,
        )
        specs[spec.name] = spec
    return specs


def get_local_model_spec(name: str) -> LocalModelSpec:
    try:
        return _load_manifest()[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown local model spec: {name}") from exc


def list_local_model_specs(kind: str | None = None) -> list[LocalModelSpec]:
    specs = list(_load_manifest().values())
    if kind:
        specs = [spec for spec in specs if spec.kind == kind]
    return specs


def models_dir() -> Path:
    root = paths.models_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def managed_model_dir(spec: LocalModelSpec) -> Path:
    return models_dir() / _slugify(spec.name) / spec.revision


def verify_local_model_dir(spec: LocalModelSpec, root: Path | None = None) -> dict[str, Any]:
    target = root or managed_model_dir(spec)
    problems: list[str] = []
    if not target.exists():
        problems.append("missing directory")
    for file_spec in spec.required_files:
        file_path = target / file_spec.path
        if not file_path.exists():
            problems.append(f"missing file:{file_spec.path}")
            continue
        size = file_path.stat().st_size
        if size != file_spec.size:
            problems.append(f"size mismatch:{file_spec.path}:{size}!={file_spec.size}")
            continue
        actual_hash = _hash_file(file_path)
        if actual_hash != file_spec.sha256:
            problems.append(f"sha256 mismatch:{file_spec.path}:{actual_hash}!={file_spec.sha256}")
    lock_path = target / MODEL_LOCK_FILENAME
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            if payload.get("revision") != spec.revision or payload.get("source_repo") != spec.source_repo:
                problems.append("lock metadata mismatch")
        except Exception:
            problems.append("invalid lock metadata")
    return {"ok": not problems, "path": str(target), "problems": problems}


def _copy_required_files(snapshot_dir: Path, target_dir: Path, spec: LocalModelSpec) -> None:
    for file_spec in spec.required_files:
        source_path = snapshot_dir / file_spec.path
        if not source_path.exists():
            raise FileNotFoundError(f"snapshot missing required file: {file_spec.path}")
        destination = target_dir / file_spec.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
    (target_dir / MODEL_LOCK_FILENAME).write_text(
        json.dumps(_lock_payload(spec), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_local_model(
    name: str,
    *,
    local_files_only: bool = False,
    force_redownload: bool = False,
) -> Path:
    spec = get_local_model_spec(name)
    target_dir = managed_model_dir(spec)
    verification = verify_local_model_dir(spec, target_dir)
    if verification["ok"] and not force_redownload:
        return target_dir
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    from huggingface_hub import snapshot_download

    cache_dir = models_dir() / "_hf-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = Path(
        snapshot_download(
            repo_id=spec.source_repo,
            revision=spec.revision,
            allow_patterns=[item.path for item in spec.required_files],
            cache_dir=str(cache_dir),
            local_files_only=local_files_only,
        )
    )

    target_parent = target_dir.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{_slugify(spec.name)}-{spec.revision[:12]}.",
            dir=str(target_parent),
        )
    )
    try:
        _copy_required_files(snapshot_dir, tmp_dir, spec)
        verification = verify_local_model_dir(spec, tmp_dir)
        if not verification["ok"]:
            raise ValueError("; ".join(verification["problems"]))
        os.replace(tmp_dir, target_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return target_dir


def build_fastembed_embedding(name: str):
    spec = get_local_model_spec(name)
    if spec.kind != "fastembed_embedding":
        raise ValueError(f"{name} is not a fastembed embedding model")
    from fastembed import TextEmbedding

    target_dir = ensure_local_model(name)
    return TextEmbedding(spec.model_id, specific_model_path=str(target_dir))


def build_fastembed_reranker(name: str):
    spec = get_local_model_spec(name)
    if spec.kind != "fastembed_reranker":
        raise ValueError(f"{name} is not a fastembed reranker")
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    target_dir = ensure_local_model(name)
    return TextCrossEncoder(spec.model_id, specific_model_path=str(target_dir))


__all__ = [
    "LocalModelFile",
    "LocalModelSpec",
    "MODEL_LOCK_FILENAME",
    "MANIFEST_PATH",
    "build_fastembed_embedding",
    "build_fastembed_reranker",
    "ensure_local_model",
    "get_local_model_spec",
    "list_local_model_specs",
    "managed_model_dir",
    "models_dir",
    "verify_local_model_dir",
]
