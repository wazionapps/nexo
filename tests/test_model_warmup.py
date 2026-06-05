from __future__ import annotations

import sys
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


def test_classifier_warmup_target_is_opt_in(monkeypatch):
    fake_spec = model_warmup.WarmupTarget(
        name="required-embedding",
        kind="fastembed_embedding",
        model_id="fake/required",
        source="tests",
        required=True,
    )
    monkeypatch.setitem(sys.modules, "local_models", type("LM", (), {
        "list_local_model_specs": staticmethod(lambda: [fake_spec]),
    }))
    monkeypatch.setitem(sys.modules, "classifier_local", type("CL", (), {
        "MODEL_ID": "fake/classifier",
        "MODEL_REVISION": "rev",
    }))
    monkeypatch.delenv("NEXO_LOCAL_CLASSIFIER", raising=False)

    assert [target.name for target in model_warmup.warmup_targets()] == ["required-embedding"]

    monkeypatch.setenv("NEXO_LOCAL_CLASSIFIER", "auto")
    assert [target.name for target in model_warmup.warmup_targets()] == [
        "local-zero-shot-classifier",
        "required-embedding",
    ]
    assert model_warmup.warmup_targets()[0].required is False

    monkeypatch.setenv("NEXO_LOCAL_CLASSIFIER", "install")
    assert [target.name for target in model_warmup.warmup_targets()] == ["required-embedding"]
