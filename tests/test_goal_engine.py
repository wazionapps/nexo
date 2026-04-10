import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def goal_engine_runtime(isolated_db):
    import db._core as db_core
    import db._goal_profiles as db_goal_profiles
    import db._protocol as db_protocol
    import db
    import plugins.goal_engine as goal_engine

    importlib.reload(db_core)
    importlib.reload(db_goal_profiles)
    importlib.reload(db_protocol)
    importlib.reload(db)
    importlib.reload(goal_engine)
    yield


def test_goal_profiles_seed_and_resolve_by_area():
    from plugins.goal_engine import handle_goal_profile_get, handle_goal_profile_list

    listed = json.loads(handle_goal_profile_list())
    assert listed["ok"] is True
    assert listed["count"] >= 4

    resolved = json.loads(handle_goal_profile_get(area="release", task_type="execute"))
    assert resolved["ok"] is True
    assert resolved["profile"]["profile_id"] == "release_safety"
    assert resolved["profile"]["resolved_by"] == "area"


def test_goal_profile_set_updates_profile_and_weights():
    from plugins.goal_engine import handle_goal_profile_set, handle_goal_profile_get

    payload = json.loads(
        handle_goal_profile_set(
            profile_id="support_trust",
            profile_name="Support trust",
            description="Prioriza preservar confianza del cliente en soporte.",
            scope_type="area",
            scope_value="support",
            goal_labels='["preserve_trust","maximise_success"]',
            weights='{"impact":0.18,"success":0.36,"risk":0.28,"somatic":0.18}',
        )
    )
    assert payload["ok"] is True
    assert payload["profile"]["profile_id"] == "support_trust"
    assert payload["profile"]["weights"]["success"] > payload["profile"]["weights"]["impact"]

    resolved = json.loads(handle_goal_profile_get(area="support", task_type="execute"))
    assert resolved["profile"]["profile_id"] == "support_trust"
    assert resolved["profile"]["goal_labels"] == ["preserve_trust", "maximise_success"]


def test_goal_engine_status_reports_readiness_gaps_before_history_exists():
    from plugins.goal_engine import handle_goal_engine_status

    payload = json.loads(handle_goal_engine_status())
    assert payload["ok"] is True
    assert payload["telemetry"]["outcomes_total"] == 0
    assert payload["telemetry"]["cortex_evaluations_total"] == 0
    assert payload["readiness"]["has_active_profiles"] is True
    assert payload["readiness"]["has_outcome_history"] is False
    assert payload["next_gaps"]
