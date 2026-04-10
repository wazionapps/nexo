import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def cortex_runtime(isolated_db):
    import db._core as db_core
    import db._protocol as db_protocol
    import db
    import plugins.cortex as cortex

    importlib.reload(db_core)
    importlib.reload(db_protocol)
    importlib.reload(db)
    importlib.reload(cortex)
    yield


def test_cortex_decide_persists_recommendation_and_scores():
    from db import get_db
    from plugins.cortex import handle_cortex_decide

    payload = json.loads(
        handle_cortex_decide(
            goal="Stabilize the release path",
            task_type="execute",
            impact_level="critical",
            area="release",
            evidence_refs='["release contract", "staging smoke"]',
            alternatives=json.dumps([
                {"name": "staged_release", "description": "Run staged release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Push straight to production and skip staged verification"},
            ]),
        )
    )

    assert payload["ok"] is True
    assert payload["recommendation"] == "staged_release"
    assert len(payload["scores"]) == 2
    row = get_db().execute(
        "SELECT recommended_choice, selected_choice, selection_source FROM cortex_evaluations WHERE id = ?",
        (payload["evaluation_id"],),
    ).fetchone()
    assert row["recommended_choice"] == "staged_release"
    assert row["selected_choice"] == "staged_release"
    assert row["selection_source"] == "recommended"


def test_cortex_override_preserves_override_reason():
    from db import get_db
    from plugins.cortex import handle_cortex_decide, handle_cortex_override

    created = json.loads(
        handle_cortex_decide(
            goal="Choose a release strategy",
            task_type="execute",
            impact_level="high",
            area="release",
            alternatives=json.dumps([
                {"name": "staged_release", "description": "Run staged release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Push straight to production and skip staged verification"},
            ]),
        )
    )
    overridden = json.loads(
        handle_cortex_override(
            evaluation_id=created["evaluation_id"],
            chosen="direct_release",
            reason="Temporary emergency window justified the riskier path.",
        )
    )

    assert overridden["ok"] is True
    row = get_db().execute(
        "SELECT selected_choice, selection_reason, selection_source FROM cortex_evaluations WHERE id = ?",
        (created["evaluation_id"],),
    ).fetchone()
    assert row["selected_choice"] == "direct_release"
    assert "emergency window" in row["selection_reason"]
    assert row["selection_source"] == "override"


def test_cortex_decide_links_pending_outcome_for_same_task():
    from db import create_outcome, get_db
    from plugins.cortex import handle_cortex_decide

    task_id = "PT-OUTCOME-LINK"
    outcome = create_outcome(
        "release_gate",
        "Cerrar release con verificación",
        "La salida queda validada",
        metric_source="protocol_task_status",
        action_id=task_id,
    )

    payload = json.loads(
        handle_cortex_decide(
            goal="Deploy the production release package",
            task_type="execute",
            impact_level="critical",
            area="release",
            task_id=task_id,
            alternatives=json.dumps([
                {"name": "canary_release", "description": "Deploy staged canary release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Deploy directly to production without staged verification"},
            ]),
        )
    )

    assert payload["ok"] is True
    assert payload["linked_outcome_id"] == outcome["id"]
    row = get_db().execute(
        "SELECT linked_outcome_id FROM cortex_evaluations WHERE id = ?",
        (payload["evaluation_id"],),
    ).fetchone()
    assert row["linked_outcome_id"] == outcome["id"]
