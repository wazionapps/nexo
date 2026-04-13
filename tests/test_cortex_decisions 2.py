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


def test_cortex_check_rejects_invalid_task_type():
    from plugins.cortex import handle_cortex_check

    payload = handle_cortex_check(
        goal="Run malformed cortex check",
        task_type="ship",
        plan='["inspect"]',
    )

    assert payload.startswith("ERROR: Invalid task_type")
    assert "Valid task types: analyze, answer, delegate, edit, execute" in payload


def test_cortex_decide_rejects_invalid_task_type():
    from plugins.cortex import handle_cortex_decide

    payload = json.loads(
        handle_cortex_decide(
            goal="Choose a release strategy",
            task_type="ship",
            impact_level="high",
            area="release",
            alternatives=json.dumps([
                {"name": "staged_release", "description": "Run staged release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Push straight to production and skip staged verification"},
            ]),
        )
    )

    assert payload["ok"] is False
    assert "Invalid task_type" in payload["error"]
    assert payload["valid_task_types"] == ["analyze", "answer", "delegate", "edit", "execute"]


def test_cortex_decide_rejects_invalid_impact_level():
    from plugins.cortex import handle_cortex_decide

    payload = json.loads(
        handle_cortex_decide(
            goal="Choose a release strategy",
            task_type="execute",
            impact_level="urgent",
            area="release",
            alternatives=json.dumps([
                {"name": "staged_release", "description": "Run staged release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Push straight to production and skip staged verification"},
            ]),
        )
    )

    assert payload["ok"] is False
    assert "Invalid impact_level" in payload["error"]
    assert payload["valid_impact_levels"] == ["critical", "high", "medium"]


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


def test_cortex_decide_uses_goal_profile_and_can_change_recommendation():
    from db import get_db
    from plugins.cortex import handle_cortex_decide

    alternatives = json.dumps([
        {
            "name": "staged_validation",
            "description": "Validate in staging with smoke tests, rollback ready, monitor and reconcile before moving.",
        },
        {
            "name": "direct_growth_push",
            "description": "Deploy release directly to production, ship fast, automate launch, integrate immediately and skip manual review.",
        },
    ])

    safety = json.loads(
        handle_cortex_decide(
            goal="Ship the public release package",
            task_type="execute",
            impact_level="critical",
            area="release",
            alternatives=alternatives,
            goal_profile_id="release_safety",
        )
    )
    growth = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )

    assert safety["ok"] is True
    assert growth["ok"] is True
    assert safety["goal_profile"]["profile_id"] == "release_safety"
    assert growth["goal_profile"]["profile_id"] == "business_growth"
    assert safety["recommendation"] == "staged_validation"
    assert growth["recommendation"] == "direct_growth_push"
    assert safety["scores"][0]["goal_profile_focus"] == "risk"
    assert growth["scores"][0]["goal_profile_focus"] == "impact"

    rows = get_db().execute(
        "SELECT goal_profile_id, goal_profile_labels, goal_profile_weights FROM cortex_evaluations ORDER BY id ASC"
    ).fetchall()
    assert rows[0]["goal_profile_id"] == "release_safety"
    assert rows[1]["goal_profile_id"] == "business_growth"
    assert "preserve_trust" in rows[0]["goal_profile_labels"]
    assert "maximise_business_impact" in rows[1]["goal_profile_labels"]


def test_cortex_decide_without_explicit_profile_resolves_context_profile():
    from plugins.cortex import handle_cortex_decide

    payload = json.loads(
        handle_cortex_decide(
            goal="Close a routine operational task",
            task_type="execute",
            impact_level="high",
            area="unknown",
            alternatives=json.dumps([
                {"name": "safe_path", "description": "Verify, test and document the change before closing it."},
                {"name": "fast_path", "description": "Ship quickly and skip manual review."},
            ]),
        )
    )

    assert payload["ok"] is True
    assert payload["goal_profile"]["profile_id"] == "ops_efficiency"
    assert payload["goal_profile"]["resolved_by"] == "task_type"


def test_cortex_history_threshold_is_visible_before_it_affects_ranking():
    from db import create_outcome, evaluate_outcome
    from plugins.cortex import handle_cortex_decide

    alternatives = json.dumps([
        {
            "name": "staged_validation",
            "description": "Validate in staging with smoke tests, rollback ready, monitor and reconcile before moving.",
        },
        {
            "name": "direct_growth_push",
            "description": "Deploy release directly to production, ship fast, automate launch, integrate immediately and skip manual review.",
        },
    ])

    missed_outcome = create_outcome(
        "manual_review",
        "Historical miss for growth push",
        "The direct growth push succeeds",
        metric_source="manual",
        target_value=1,
        target_operator="gte",
        deadline="2000-01-01T00:00:00",
    )
    seeded = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            linked_outcome_id=missed_outcome["id"],
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )
    assert seeded["recommendation"] == "direct_growth_push"
    evaluate_outcome(missed_outcome["id"], actual_value=0.0)

    fresh = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )

    assert fresh["ok"] is True
    assert fresh["recommendation"] == "direct_growth_push"
    direct = next(item for item in fresh["scores"] if item["name"] == "direct_growth_push")
    assert direct["historical_signal"]["resolved_outcomes"] == 1
    assert direct["historical_signal"]["threshold"] == 2
    assert direct["historical_signal"]["active"] is False


def test_cortex_history_can_flip_recommendation_with_resolved_outcomes():
    from db import create_outcome, evaluate_outcome
    from plugins.cortex import handle_cortex_decide, handle_cortex_override

    alternatives = json.dumps([
        {
            "name": "staged_validation",
            "description": "Validate in staging with smoke tests, rollback ready, monitor and reconcile before moving.",
        },
        {
            "name": "direct_growth_push",
            "description": "Deploy release directly to production, ship fast, automate launch, integrate immediately and skip manual review.",
        },
    ])

    for idx in range(2):
        missed_outcome = create_outcome(
            "manual_review",
            f"Historical miss for growth push #{idx}",
            "The direct growth push succeeds",
            metric_source="manual",
            target_value=1,
            target_operator="gte",
            deadline="2000-01-01T00:00:00",
        )
        seeded = json.loads(
            handle_cortex_decide(
                goal="Maximize growth from this launch window",
                task_type="execute",
                impact_level="critical",
                area="business",
                linked_outcome_id=missed_outcome["id"],
                alternatives=alternatives,
                goal_profile_id="business_growth",
            )
        )
        assert seeded["recommendation"] == "direct_growth_push"
        evaluate_outcome(missed_outcome["id"], actual_value=0.0)

    for idx in range(2):
        met_outcome = create_outcome(
            "manual_review",
            f"Historical success for staged validation #{idx}",
            "The staged validation succeeds",
            metric_source="manual",
            target_value=1,
            target_operator="gte",
            deadline="2099-01-01T00:00:00",
        )
        seeded = json.loads(
            handle_cortex_decide(
                goal="Maximize growth from this launch window",
                task_type="execute",
                impact_level="critical",
                area="business",
                linked_outcome_id=met_outcome["id"],
                alternatives=alternatives,
                goal_profile_id="business_growth",
            )
        )
        overridden = json.loads(
            handle_cortex_override(
                evaluation_id=seeded["evaluation_id"],
                chosen="staged_validation",
                reason="Se eligió validación escalonada para proteger reputación y fiabilidad.",
            )
        )
        assert overridden["ok"] is True
        evaluate_outcome(met_outcome["id"], actual_value=1.0)

    fresh = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )

    assert fresh["ok"] is True
    assert fresh["recommendation"] == "staged_validation"
    staged = next(item for item in fresh["scores"] if item["name"] == "staged_validation")
    direct = next(item for item in fresh["scores"] if item["name"] == "direct_growth_push")
    assert staged["historical_signal"]["active"] is True
    assert staged["historical_signal"]["met"] == 2
    assert direct["historical_signal"]["active"] is True
    assert direct["historical_signal"]["missed"] == 2
    assert staged["total_score"] > direct["total_score"]


def test_cortex_uses_captured_outcome_pattern_learning_in_following_decision():
    from db import (
        create_outcome,
        evaluate_outcome,
        list_outcome_pattern_candidates,
        capture_outcome_pattern,
    )
    from plugins.cortex import handle_cortex_decide

    alternatives = json.dumps([
        {
            "name": "staged_validation",
            "description": "Validate in staging with smoke tests, rollback ready, monitor and reconcile before moving.",
        },
        {
            "name": "direct_growth_push",
            "description": "Deploy release directly to production, ship fast, automate launch, integrate immediately and skip manual review.",
        },
    ])

    for choice in ("staged_validation", "direct_growth_push"):
        for idx in range(3):
            met_outcome = create_outcome(
                "manual_review",
                f"Structured pattern seed for {choice} #{idx}",
                f"The strategy {choice} succeeds",
                metric_source="manual",
                target_value=1,
                target_operator="gte",
                deadline="2099-01-01T00:00:00",
            )
            seeded = json.loads(
                handle_cortex_decide(
                    goal="Maximize growth from this launch window",
                    task_type="execute",
                    impact_level="critical",
                    area="business",
                    linked_outcome_id=met_outcome["id"],
                    alternatives=alternatives,
                    goal_profile_id="business_growth",
                )
            )
            if seeded["recommendation"] != choice:
                # Keep the seed aligned with the intended selected_choice.
                from plugins.cortex import handle_cortex_override
                override = json.loads(
                    handle_cortex_override(
                        evaluation_id=seeded["evaluation_id"],
                        chosen=choice,
                        reason=f"Seed the structured pattern for {choice}.",
                    )
                )
                assert override["ok"] is True
            evaluate_outcome(met_outcome["id"], actual_value=1.0)

    before = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )
    staged_before = next(item for item in before["scores"] if item["name"] == "staged_validation")
    direct_before = next(item for item in before["scores"] if item["name"] == "direct_growth_push")
    assert staged_before["pattern_learning_signal"]["active"] is False
    assert direct_before["pattern_learning_signal"]["active"] is False

    candidate = next(
        item
        for item in list_outcome_pattern_candidates(min_resolved=3, limit=10)
        if item["selected_choice"] == "staged_validation"
        and item["area"] == "business"
        and item["goal_profile_id"] == "business_growth"
    )
    captured = capture_outcome_pattern(candidate["pattern_key"])
    assert captured["ok"] is True

    after = json.loads(
        handle_cortex_decide(
            goal="Maximize growth from this launch window",
            task_type="execute",
            impact_level="critical",
            area="business",
            alternatives=alternatives,
            goal_profile_id="business_growth",
        )
    )
    staged_after = next(item for item in after["scores"] if item["name"] == "staged_validation")
    direct_after = next(item for item in after["scores"] if item["name"] == "direct_growth_push")

    assert staged_after["pattern_learning_signal"]["active"] is True
    assert staged_after["pattern_learning_signal"]["mode"] == "prefer"
    assert staged_after["pattern_learning_signal"]["learning_id"] == captured["learning"]["id"]
    assert staged_after["total_score"] > staged_before["total_score"]
    assert direct_after["pattern_learning_signal"]["active"] is False


def test_cortex_quality_summarises_acceptance_override_and_linked_outcomes():
    from db import create_outcome, evaluate_outcome
    from plugins.cortex import handle_cortex_decide, handle_cortex_override, handle_cortex_quality

    met_outcome = create_outcome(
        "manual_review",
        "Confirmar estrategia recomendada",
        "El outcome recomendado se cumple",
        metric_source="manual",
        target_value=1,
        target_operator="gte",
        deadline="2099-01-01T00:00:00",
    )
    missed_outcome = create_outcome(
        "manual_review",
        "Confirmar estrategia override",
        "El outcome override se cumple",
        metric_source="manual",
        target_value=1,
        target_operator="gte",
        deadline="2000-01-01T00:00:00",
    )

    recommended = json.loads(
        handle_cortex_decide(
            goal="Cerrar un release de forma segura",
            task_type="execute",
            impact_level="critical",
            area="release",
            linked_outcome_id=met_outcome["id"],
            alternatives=json.dumps([
                {"name": "staged_validation", "description": "Validate in staging with smoke tests and rollback ready."},
                {"name": "direct_push", "description": "Deploy directly to production and skip manual review."},
            ]),
            goal_profile_id="release_safety",
        )
    )

    overridden = json.loads(
        handle_cortex_decide(
            goal="Cerrar la ventana de growth de hoy",
            task_type="execute",
            impact_level="critical",
            area="business",
            linked_outcome_id=missed_outcome["id"],
            alternatives=json.dumps([
                {"name": "staged_validation", "description": "Validate in staging with smoke tests and rollback ready."},
                {"name": "direct_growth_push", "description": "Deploy release directly to production, ship fast and skip manual review."},
            ]),
            goal_profile_id="business_growth",
        )
    )
    assert recommended["ok"] is True
    assert overridden["ok"] is True

    override_result = json.loads(
        handle_cortex_override(
            evaluation_id=overridden["evaluation_id"],
            chosen="staged_validation",
            reason="Se prefirió la opción segura por contexto reputacional.",
        )
    )
    assert override_result["ok"] is True

    evaluate_outcome(met_outcome["id"], actual_value=1.0)
    evaluate_outcome(missed_outcome["id"], actual_value=0.0)

    summary = json.loads(handle_cortex_quality(days=30))
    assert summary["ok"] is True
    payload = summary["summary"]
    assert payload["total_evaluations"] == 2
    assert payload["accepted_recommendations"] == 1
    assert payload["overrides"] == 1
    assert payload["recommendation_accept_rate"] == 50.0
    assert payload["override_rate"] == 50.0
    assert payload["linked_outcomes_total"] == 2
    assert payload["linked_outcomes_met"] == 1
    assert payload["linked_outcomes_missed"] == 1
    assert payload["recommended_success_rate"] == 100.0
    assert payload["override_success_rate"] == 0.0
    assert payload["top_goal_profiles"][0]["goal_profile_id"] in {"business_growth", "release_safety"}
