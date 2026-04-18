"""Plan Consolidado 0.2 — cognitive_sentiment must return structured shape.

Contract:
    detect_sentiment(text) returns dict with:
      - is_correction: bool
      - valence: float in [-1.0, 1.0]
      - intent: str in SENTIMENT_INTENTS

Accuracy target: >=80% on the 10 labelled fixtures below (8/10).
"""

from cognitive._trust import detect_sentiment, SENTIMENT_INTENTS


# Real-world-ish fixtures: (text, expected is_correction, expected intent).
# Mixed ES/EN, short/long, caps/no-caps.
FIXTURES = [
    ("no es así, revisa el numero otra vez", True, "correction"),
    ("eso está mal, la URL era la otra", True, "correction"),
    ("perfecto, eso era exactamente lo que quería", False, "acknowledgement"),
    ("¿puedes explicarme cómo funciona el classifier?", False, "question"),
    ("haz el deploy ya", False, "instruction"),
    ("URGENTE, corre que se cae producción", False, "urgency"),
    ("great job, this looks good", False, "acknowledgement"),
    ("that's wrong, the endpoint is /api/v2", True, "correction"),
    ("how do I enable the beta channel in desktop?", False, "question"),
    ("gracias, ha quedado genial", False, "acknowledgement"),
]


def test_shape_is_correction_valence_intent_present_for_every_input():
    for text, _, _ in FIXTURES:
        res = detect_sentiment(text)
        assert "is_correction" in res, f"missing is_correction for {text!r}"
        assert "valence" in res, f"missing valence for {text!r}"
        assert "intent" in res, f"missing intent for {text!r}"
        assert isinstance(res["is_correction"], bool)
        assert isinstance(res["valence"], float)
        assert -1.0 <= res["valence"] <= 1.0
        assert res["intent"] in SENTIMENT_INTENTS


def test_empty_text_returns_neutral_shape():
    res = detect_sentiment("")
    assert res["is_correction"] is False
    assert res["valence"] == 0.0
    assert res["intent"] == "neutral"


def test_accuracy_is_correction_and_intent_meets_80pct():
    correct_is_correction = 0
    correct_intent = 0
    total = len(FIXTURES)

    for text, expected_correction, expected_intent in FIXTURES:
        res = detect_sentiment(text)
        if res["is_correction"] == expected_correction:
            correct_is_correction += 1
        if res["intent"] == expected_intent:
            correct_intent += 1

    # Accept >=80% on each.
    assert correct_is_correction / total >= 0.8, (
        f"is_correction accuracy only {correct_is_correction}/{total}"
    )
    assert correct_intent / total >= 0.8, (
        f"intent accuracy only {correct_intent}/{total}"
    )
