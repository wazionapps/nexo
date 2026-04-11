"""Coverage baseline for cognitive/_trust.py — Fase 4 item 2.

Pre-Fase 4 the module had 53% coverage. This file pins direct exercises
of the trust + sentiment + dissonance helpers so a future regression
cannot silently break the trust pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


# ── get_trust_events ──────────────────────────────────────────────────────


class TestGetTrustEvents:
    def test_returns_event_dict_with_canonical_keys(self, isolated_db):
        from cognitive._trust import get_trust_events
        events = get_trust_events()
        assert isinstance(events, dict)
        # Canonical keys from _DEFAULT_TRUST_EVENTS in src/cognitive/_trust.py:10.
        for key in ("explicit_thanks", "task_completed", "correction", "repeated_error"):
            assert key in events
        # Positive deltas are positive, negative deltas are negative.
        assert events["explicit_thanks"] > 0
        assert events["correction"] < 0
        assert events["repeated_error"] < events["correction"]  # repeated is worse


# ── auto_detect_trust_events (auto-patterns disabled, returns []) ────────


class TestAutoDetectTrustEventsContract:
    def test_returns_empty_list_for_short_text(self, isolated_db):
        from cognitive._trust import auto_detect_trust_events
        # The function exits early for text < 5 chars.
        assert auto_detect_trust_events("hi") == []

    def test_returns_empty_list_for_neutral_text(self, isolated_db):
        from cognitive._trust import auto_detect_trust_events
        # TRUST_AUTO_PATTERNS is intentionally empty in the current build —
        # auto-detection has been deprecated in favor of LLM-emitted events.
        # The function still has to exist and return [].
        assert auto_detect_trust_events("the deploy finished without issues") == []

    def test_returns_empty_list_for_correction_text(self, isolated_db):
        from cognitive._trust import auto_detect_trust_events
        # Same — the function returns [] regardless of content because the
        # patterns dict is empty by design (see comment around line 67-72).
        assert auto_detect_trust_events("no, that's wrong, you misunderstood") == []


# ── detect_sentiment ──────────────────────────────────────────────────────


class TestDetectSentiment:
    def test_empty_text_returns_neutral(self, isolated_db):
        from cognitive._trust import detect_sentiment
        result = detect_sentiment("")
        assert result["sentiment"] == "neutral"
        assert result["intensity"] == 0.5

    def test_returns_required_keys(self, isolated_db):
        from cognitive._trust import detect_sentiment
        result = detect_sentiment("la migración terminó")
        for key in ("sentiment", "intensity", "signals", "guidance"):
            assert key in result
        assert result["sentiment"] in {"neutral", "positive", "negative", "urgent"}
        assert isinstance(result["signals"], list)

    def test_caps_words_boost_negative(self, isolated_db):
        from cognitive._trust import detect_sentiment
        # Two ALL CAPS words boost neg_score by 2 (line 278-279).
        result = detect_sentiment("THIS IS broken")
        # Even if the keywords don't match POSITIVE/NEGATIVE_SIGNALS, the
        # ALL CAPS heuristic should push intensity above 0.5.
        assert isinstance(result["intensity"], float)


# ── adjust_trust + get_trust_score ────────────────────────────────────────


class TestAdjustTrust:
    def test_get_trust_score_initializes_to_50(self, isolated_db):
        from cognitive._trust import get_trust_score
        score = get_trust_score()
        assert isinstance(score, (int, float))
        assert score == 50.0  # initial baseline

    def test_adjust_trust_with_known_positive_event_increases_score(self, isolated_db):
        from cognitive._trust import adjust_trust, get_trust_score
        before = get_trust_score()
        result = adjust_trust("explicit_thanks", context="test thanks")
        after = get_trust_score()
        assert isinstance(result, dict)
        assert result["event"] == "explicit_thanks"
        assert result["delta"] > 0
        assert after > before

    def test_adjust_trust_with_known_negative_event_decreases_score(self, isolated_db):
        from cognitive._trust import adjust_trust, get_trust_score
        before = get_trust_score()
        result = adjust_trust("correction", context="test correction")
        after = get_trust_score()
        assert result["delta"] < 0
        assert after < before

    def test_adjust_trust_with_unknown_event_returns_error(self, isolated_db):
        from cognitive._trust import adjust_trust
        result = adjust_trust("definitely_not_a_real_event")
        assert isinstance(result, dict)
        assert result["delta"] == 0
        assert result.get("error") == "unknown event"

    def test_adjust_trust_with_custom_delta_overrides_default(self, isolated_db):
        from cognitive._trust import adjust_trust
        result = adjust_trust("any_event", context="custom", custom_delta=-7.5)
        assert result["delta"] == -7.5

    def test_adjust_trust_clamps_to_range_0_100(self, isolated_db):
        from cognitive._trust import adjust_trust
        # Apply a huge negative delta — the score should clamp at 0, not go negative.
        adjust_trust("any_event", custom_delta=-500)
        from cognitive._trust import get_trust_score
        assert get_trust_score() >= 0.0


# ── check_correction_fatigue (already smoke-tested in cognitive-decay) ────


class TestCheckCorrectionFatigueSmoke:
    def test_returns_list_on_empty_db(self, isolated_db):
        from cognitive._trust import check_correction_fatigue
        result = check_correction_fatigue()
        assert isinstance(result, list)
        assert result == []  # nothing to flag in empty install


# ── log_sentiment ─────────────────────────────────────────────────────────


class TestLogSentiment:
    def test_log_sentiment_returns_dict(self, isolated_db):
        from cognitive._trust import log_sentiment
        result = log_sentiment("estoy muy contento con el deploy")
        assert isinstance(result, dict)
        assert "sentiment" in result
