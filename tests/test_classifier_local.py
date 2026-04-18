"""Plan 0.21 — classifier_local skeleton contract.

Does NOT download the model in CI; verifies the fail-closed paths so
Guardian rules degrade gracefully when transformers is absent.
"""

from __future__ import annotations


def test_model_pin_matches_docs():
    from pathlib import Path

    import classifier_local

    notes = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "classifier-model-notes.md"
    ).read_text(encoding="utf-8")
    assert classifier_local.MODEL_ID in notes
    assert classifier_local.MODEL_REVISION in notes


def test_classify_returns_none_when_pipeline_unavailable(monkeypatch):
    import classifier_local

    clf = classifier_local.LocalZeroShotClassifier()
    # Simulate absence by forcing _load_failed before calling classify.
    clf._load_failed = True
    res = clf.classify("lo hemos dejado, ya estaría", ["done_claim", "noise"])
    assert res is None


def test_classify_fail_closed_returns_fallback_when_unavailable():
    import classifier_local

    clf = classifier_local.LocalZeroShotClassifier()
    clf._load_failed = True  # pipeline never loads
    res = clf.classify_fail_closed(
        "lo hemos dejado, ya estaría",
        ["done_claim", "noise"],
        fallback_label="noise",
    )
    assert res.label == "noise"
    assert res.confidence == 0.0
    assert set(res.scores.keys()) == {"done_claim", "noise"}


def test_empty_inputs_return_none():
    import classifier_local

    clf = classifier_local.LocalZeroShotClassifier()
    clf._load_failed = True
    assert clf.classify("", ["a", "b"]) is None
    assert clf.classify("text", []) is None
