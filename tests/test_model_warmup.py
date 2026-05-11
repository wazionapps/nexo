from __future__ import annotations

from argparse import Namespace

import model_warmup


def test_strict_warmup_ignores_optional_model_failures(monkeypatch):
    optional = model_warmup.WarmupTarget(
        name="optional-local-presence",
        kind="local_presence_llm",
        model_id="fake/optional",
        source="tests",
        required=False,
    )
    required = model_warmup.WarmupTarget(
        name="required-embedding",
        kind="fastembed_embedding",
        model_id="fake/required",
        source="tests",
        required=True,
    )

    monkeypatch.setattr(model_warmup, "warmup_targets", lambda: [required, optional])
    monkeypatch.setattr(model_warmup, "_write_state", lambda payload: None)

    def fake_warm(target):
        if target.name == optional.name:
            raise RuntimeError("offline optional model missing")

    monkeypatch.setattr(model_warmup, "warm_target", fake_warm)

    rc = model_warmup.run(Namespace(dry_run=False, json=False, strict=True))
    assert rc == 0


def test_strict_warmup_still_fails_on_required_model_errors(monkeypatch):
    required = model_warmup.WarmupTarget(
        name="required-embedding",
        kind="fastembed_embedding",
        model_id="fake/required",
        source="tests",
        required=True,
    )

    monkeypatch.setattr(model_warmup, "warmup_targets", lambda: [required])
    monkeypatch.setattr(model_warmup, "_write_state", lambda payload: None)
    monkeypatch.setattr(
        model_warmup,
        "warm_target",
        lambda target: (_ for _ in ()).throw(RuntimeError("required model missing")),
    )

    rc = model_warmup.run(Namespace(dry_run=False, json=False, strict=True))
    assert rc == 1
