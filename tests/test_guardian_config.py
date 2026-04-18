"""Tests for guardian_config loader + validator (Fase 2 spec 0.5 + 0.19)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path


def test_default_config_validates_clean():
    from guardian_config import load_default_guardian_config, validate_guardian_config
    cfg = load_default_guardian_config()
    errors = validate_guardian_config(cfg)
    assert errors == [], f"packaged default invalid: {errors}"


def test_core_rule_off_rejected():
    from guardian_config import CORE_RULES, validate_guardian_config
    base_rules = {rid: "hard" for rid in CORE_RULES}
    base_rules["R13_pre_edit_guard"] = "off"
    errors = validate_guardian_config({"rules": base_rules})
    assert any("core rule" in e and "cannot be disabled" in e for e in errors)


def test_all_core_rules_required():
    from guardian_config import CORE_RULES, validate_guardian_config
    base_rules = {rid: "hard" for rid in CORE_RULES if rid != "R13_pre_edit_guard"}
    errors = validate_guardian_config({"rules": base_rules})
    assert any("core rules missing" in e for e in errors)


def test_invalid_mode_rejected():
    from guardian_config import CORE_RULES, validate_guardian_config
    rules = {rid: "hard" for rid in CORE_RULES}
    rules["R13_pre_edit_guard"] = "turbo"
    errors = validate_guardian_config({"rules": rules})
    assert any("invalid mode" in e for e in errors)


def test_fail_closed_timeout_must_be_positive():
    from guardian_config import CORE_RULES, validate_guardian_config
    rules = {rid: "hard" for rid in CORE_RULES}
    errors = validate_guardian_config({
        "rules": rules,
        "fail_closed": {"classifier_timeout_seconds": -1},
    })
    assert any("must be > 0" in e for e in errors)


def test_rule_mode_defence_in_depth():
    """Even if validation somehow passes, rule_mode must not return 'off' for a core rule."""
    from guardian_config import rule_mode
    cfg = {
        "rules": {"R13_pre_edit_guard": "off"},
        "runtime_overrides": {"enabled": False},
    }
    assert rule_mode(cfg, "R13_pre_edit_guard") != "off"


def test_rule_mode_unknown_returns_default():
    from guardian_config import rule_mode
    cfg = {"rules": {}, "runtime_overrides": {"enabled": False}}
    assert rule_mode(cfg, "R_whatever") == "shadow"
    assert rule_mode(cfg, "R_whatever", default="soft") == "soft"


def test_load_falls_back_to_default_when_user_missing(tmp_path):
    from guardian_config import load_guardian_config
    user = tmp_path / "guardian.json"
    cfg = load_guardian_config(user_path=user)
    assert "rules" in cfg
    assert "R13_pre_edit_guard" in cfg["rules"]


def test_merge_fills_in_new_default_rules(tmp_path):
    """A future release adding a rule must NOT let the user's older config run it as off."""
    from guardian_config import load_guardian_config, load_default_guardian_config
    user = tmp_path / "guardian.json"
    # User config with only a subset of rules
    user.write_text(json.dumps({
        "rules": {"R13_pre_edit_guard": "shadow"},
    }))
    cfg = load_guardian_config(user_path=user, strict=False)
    # R13 retains user's "shadow"
    assert cfg["rules"]["R13_pre_edit_guard"] == "shadow"
    # A rule the user did NOT have comes from defaults (hard/soft), NOT missing
    defaults = load_default_guardian_config()
    assert cfg["rules"]["R14_correction_learning"] == defaults["rules"]["R14_correction_learning"]
