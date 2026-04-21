from __future__ import annotations

import json

from calibration_runtime import load_runtime_calibration


def test_load_runtime_calibration_keeps_runtime_keys_only(tmp_path):
    target = tmp_path / "calibration.json"
    target.write_text(json.dumps({
        "user": {"name": "Francisco", "assistant_name": "Nero", "language": "es"},
        "preferences": {"default_resonance": "alto"},
        "meta": {"role": "operator"},
        "calibration_log": [{"date": "2026-04-21", "recommendation": "keep"}],
        "mood_history": [{"date": "2026-04-21", "score": 80}],
        "calibration_notes": [{"date": "2026-04-21", "recommendation": "note"}],
        "version": 1,
    }))

    payload = load_runtime_calibration(target)

    assert payload["user"]["assistant_name"] == "Nero"
    assert payload["preferences"]["default_resonance"] == "alto"
    assert payload["meta"]["role"] == "operator"
    assert payload["version"] == 1
    assert "calibration_log" not in payload
    assert "mood_history" not in payload
    assert "calibration_notes" not in payload


def test_load_runtime_calibration_is_tolerant_of_missing_or_bad_files(tmp_path):
    missing = tmp_path / "missing.json"
    assert load_runtime_calibration(missing) == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{not-json")
    assert load_runtime_calibration(broken) == {}
