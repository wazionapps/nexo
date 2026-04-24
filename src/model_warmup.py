"""Predownload the local models used by NEXO Brain/Desktop flows.

The installer invokes this script after Python dependencies are present.
``--dry-run`` is intentionally dependency-free so package tests can verify the
contract without downloading 1+ GB of weights.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@dataclass(frozen=True)
class WarmupTarget:
    name: str
    kind: str
    model_id: str
    source: str
    source_repo: str | None = None
    revision: str | None = None
    required: bool = True


def warmup_targets() -> list[WarmupTarget]:
    from classifier_local import MODEL_ID, MODEL_REVISION
    from local_models import list_local_model_specs

    targets = [
        WarmupTarget(
            name="local-zero-shot-classifier",
            kind="transformers_sequence_classifier",
            model_id=MODEL_ID,
            revision=MODEL_REVISION,
            source="src/classifier_local.py",
            source_repo=MODEL_ID,
        ),
    ]
    for spec in list_local_model_specs():
        targets.append(
            WarmupTarget(
                name=spec.name,
                kind=spec.kind,
                model_id=spec.model_id,
                revision=spec.revision,
                source=spec.source,
                source_repo=spec.source_repo,
            )
        )
    return targets


def _state_path() -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", "~/.nexo")).expanduser()
    return nexo_home / "runtime" / "operations" / "model-warmup-state.json"


def _write_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _warm_transformers(target: WarmupTarget) -> None:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    kwargs: dict[str, str] = {}
    if target.revision:
        kwargs["revision"] = target.revision
    AutoTokenizer.from_pretrained(target.model_id, **kwargs)
    AutoModelForSequenceClassification.from_pretrained(target.model_id, **kwargs)


def _warm_fastembed_embedding(target: WarmupTarget) -> None:
    from local_models import build_fastembed_embedding

    model = build_fastembed_embedding(target.name)
    list(model.embed(["NEXO model warmup"]))


def _warm_fastembed_reranker(target: WarmupTarget) -> None:
    from local_models import build_fastembed_reranker

    build_fastembed_reranker(target.name)


def warm_target(target: WarmupTarget) -> None:
    if target.kind == "transformers_sequence_classifier":
        _warm_transformers(target)
        return
    if target.kind == "fastembed_embedding":
        _warm_fastembed_embedding(target)
        return
    if target.kind == "fastembed_reranker":
        _warm_fastembed_reranker(target)
        return
    raise ValueError(f"unknown warmup target kind: {target.kind}")


def target_to_json(target: WarmupTarget) -> dict[str, Any]:
    return {key: value for key, value in asdict(target).items() if value is not None}


def run(args: argparse.Namespace) -> int:
    targets = warmup_targets()
    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "targets": [target_to_json(target) for target in targets],
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for target in targets:
                revision = f"@{target.revision}" if target.revision else ""
                print(f"{target.name}: {target.model_id}{revision}")
        return 0

    started = time.time()
    results: list[dict[str, Any]] = []
    ok = True
    for target in targets:
        item = target_to_json(target)
        item["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            if not args.json:
                revision = f"@{target.revision}" if target.revision else ""
                print(f"[model-warmup] {target.name}: {target.model_id}{revision}", flush=True)
            warm_target(target)
            item["ok"] = True
        except Exception as exc:  # pragma: no cover - depends on host/network/cache
            item["ok"] = False
            item["error"] = str(exc)
            if target.required:
                ok = False
            if not args.json:
                print(f"[model-warmup] FAILED {target.name}: {exc}", file=sys.stderr, flush=True)
        item["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        results.append(item)

    payload = {
        "ok": ok,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targets": results,
    }
    _write_state(payload)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if ok or not args.strict else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Predownload NEXO local model weights.")
    parser.add_argument("--dry-run", action="store_true", help="List model targets without importing ML dependencies.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any required model fails.")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
