from __future__ import annotations

from guardrails.minimal_delta import check_diff, classify_request, count_changed_lines, is_ui_path


def test_classifies_punctual_ui_requests_in_spanish_and_english() -> None:
    assert classify_request("añade el texto Cargando... al splash")
    assert classify_request("change the color of this button")
    assert classify_request("refactor the release pipeline") is None


def test_non_ui_targets_are_not_blocked() -> None:
    decision = check_diff(
        "change the text",
        "old\n",
        "new\nwith more data\n",
        "src/server.py",
    )

    assert decision.decision == "allow"
    assert "not a UI surface" in decision.reason


def test_large_punctual_ui_diff_is_blocked() -> None:
    old = "\n".join(f"line {idx}" for idx in range(12))
    new = "\n".join(f"changed {idx}" for idx in range(12))

    decision = check_diff("add the text Loading...", old, new, "renderer/App.jsx")

    assert decision.decision == "block"
    assert decision.changed_lines > decision.threshold


def test_small_punctual_ui_diff_warns_inside_soft_envelope() -> None:
    old = "a\nb\nc\nd\n"
    new = "a\nB\nc\nD\n"

    decision = check_diff("fix the wording", old, new, "renderer/App.jsx")

    assert decision.decision == "warn"
    assert decision.changed_lines == count_changed_lines(old, new)


def test_ui_path_detection() -> None:
    assert is_ui_path("renderer/App.jsx")
    assert is_ui_path("index.html")
    assert not is_ui_path("src/server.py")
