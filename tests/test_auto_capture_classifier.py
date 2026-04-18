"""Plan Consolidado 0.21 consumer — auto_capture uses classifier_local.

Verifies:
  * When regex hits, classifier is NOT consulted (fast-path remains).
  * When regex misses and classifier votes with confidence >= floor
    for a real bucket, the line is classified.
  * When classifier reports low confidence or None, regex result stands
    (no spurious capture).
  * When transformers / classifier_local fails to load, the hook keeps
    running on regex only (fail-open to original behaviour).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from hooks import auto_capture  # noqa: E402


def _reset_classifier_cache():
    auto_capture._zs_classifier = None


def test_regex_hit_does_not_consult_classifier(monkeypatch):
    _reset_classifier_cache()
    calls: list[str] = []

    def spy_get_classifier():
        calls.append("called")
        return None

    monkeypatch.setattr(auto_capture, "_get_zs_classifier", spy_get_classifier)
    facts = auto_capture._classify_line(
        "decided to switch the deploy pipeline to Cloudflare next week"
    )
    assert facts and facts[0][0] == "decision"
    assert calls == [], "classifier should not run when regex already matched"


def test_classifier_miss_is_noop(monkeypatch):
    _reset_classifier_cache()

    class FakeClf:
        def classify(self, text, labels):
            return None  # classifier unavailable this call

    monkeypatch.setattr(auto_capture, "_get_zs_classifier", lambda: FakeClf())
    facts = auto_capture._classify_line(
        "creo que el par\u00e1metro que pusiste al desplegar fue el opuesto al que correspond\u00eda"
    )
    # regex does not catch this exact phrasing and classifier returned None
    assert facts == []


def test_classifier_high_confidence_correction_is_captured(monkeypatch):
    _reset_classifier_cache()

    class FakeClf:
        def classify(self, text, labels):
            return SimpleNamespace(label="correction", confidence=0.82, scores={}, latency_ms=120)

    monkeypatch.setattr(auto_capture, "_get_zs_classifier", lambda: FakeClf())
    facts = auto_capture._classify_line(
        "creo que el par\u00e1metro que pusiste al desplegar fue el opuesto al que correspond\u00eda"
    )
    assert facts
    assert facts[0][0] == "correction"


def test_classifier_low_confidence_is_discarded(monkeypatch):
    _reset_classifier_cache()

    class FakeClf:
        def classify(self, text, labels):
            return SimpleNamespace(label="correction", confidence=0.40, scores={}, latency_ms=120)

    monkeypatch.setattr(auto_capture, "_get_zs_classifier", lambda: FakeClf())
    facts = auto_capture._classify_line(
        "creo que el par\u00e1metro que pusiste al desplegar fue el opuesto al que correspond\u00eda"
    )
    assert facts == []  # below 0.65 floor, regex had no hit → silent


def test_classifier_noise_label_is_not_captured(monkeypatch):
    _reset_classifier_cache()

    class FakeClf:
        def classify(self, text, labels):
            return SimpleNamespace(label="noise", confidence=0.90, scores={}, latency_ms=120)

    monkeypatch.setattr(auto_capture, "_get_zs_classifier", lambda: FakeClf())
    facts = auto_capture._classify_line(
        "creo que el par\u00e1metro que pusiste al desplegar fue el opuesto al que correspond\u00eda"
    )
    assert facts == []


def test_classifier_unavailable_falls_back_to_regex(monkeypatch):
    _reset_classifier_cache()
    monkeypatch.setattr(auto_capture, "_get_zs_classifier", lambda: None)
    facts = auto_capture._classify_line(
        "decided to switch the deploy pipeline to Cloudflare next week"
    )
    assert facts and facts[0][0] == "decision"


def test_short_lines_never_invoke_classifier(monkeypatch):
    _reset_classifier_cache()

    called: list[str] = []
    monkeypatch.setattr(
        auto_capture,
        "_get_zs_classifier",
        lambda: called.append("c") or None,  # type: ignore[func-returns-value]
    )
    # Line length under _ZS_MIN_LEN_FOR_LLM (40 chars).
    auto_capture._classify_line("no funciona aun")
    assert called == []
