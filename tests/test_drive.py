"""Tests for the Drive/Curiosity signal layer."""
from __future__ import annotations

import json

from db import (
    create_drive_signal, reinforce_drive_signal, get_drive_signals,
    get_drive_signal, update_drive_signal_status, decay_drive_signals,
    find_similar_drive_signal, drive_signal_stats,
)
from tools_drive import (
    detect_drive_signal,
    handle_drive_signals, handle_drive_reinforce,
    handle_drive_act, handle_drive_dismiss,
)


# ── DB layer tests ───────────────────────────────────────────────────

class TestCreateSignal:
    def test_basic_create(self):
        result = create_drive_signal("anomaly", "heartbeat", "CPC subió 40% sin cambios")
        assert result["ok"]
        assert result["tension"] == 0.3
        assert result["status"] == "latent"

    def test_invalid_type_rejected(self):
        result = create_drive_signal("invalid_type", "heartbeat", "test")
        assert not result["ok"]
        assert "Invalid signal_type" in result.get("error", "")

    def test_tension_clamped(self):
        result = create_drive_signal("anomaly", "test", "overflow", tension=5.0)
        assert result["ok"]
        assert result["tension"] == 1.0

    def test_retrievable_after_create(self):
        result = create_drive_signal("gap", "task_close", "No model for ICNEA pricing", area="canaririural")
        signal = get_drive_signal(result["id"])
        assert signal is not None
        assert signal["signal_type"] == "gap"
        assert signal["area"] == "canaririural"
        assert signal["status"] == "latent"


class TestReinforce:
    def test_tension_increases(self):
        created = create_drive_signal("pattern", "heartbeat", "María pregunta lo mismo cada trimestre")
        result = reinforce_drive_signal(created["id"], "María preguntó otra vez por lo mismo")
        assert result["ok"]
        assert result["new_tension"] > result["old_tension"]

    def test_promotes_to_rising(self):
        created = create_drive_signal("anomaly", "test", "test signal", tension=0.3)
        result = reinforce_drive_signal(created["id"], "second observation")
        assert result["new_status"] in ("rising", "ready")

    def test_promotes_to_ready_on_three_reinforcements(self):
        created = create_drive_signal("pattern", "test", "recurring issue", tension=0.2)
        reinforce_drive_signal(created["id"], "obs 2")
        result = reinforce_drive_signal(created["id"], "obs 3")
        assert result["new_status"] == "ready"
        assert result["evidence_count"] >= 3

    def test_cannot_reinforce_acted(self):
        created = create_drive_signal("anomaly", "test", "already done")
        update_drive_signal_status(created["id"], "acted", "investigated")
        result = reinforce_drive_signal(created["id"], "new obs")
        assert not result["ok"]

    def test_cannot_reinforce_dismissed(self):
        created = create_drive_signal("anomaly", "test", "dismissed one")
        update_drive_signal_status(created["id"], "dismissed", "not worth it")
        result = reinforce_drive_signal(created["id"], "new obs")
        assert not result["ok"]


class TestDecay:
    def test_decay_reduces_tension(self):
        created = create_drive_signal("anomaly", "test", "will decay", tension=0.5, decay_rate=0.1)
        result = decay_drive_signals()
        assert result["decayed"] >= 1
        signal = get_drive_signal(created["id"])
        assert signal is not None
        assert float(signal["tension"]) < 0.5

    def test_decay_kills_weak_signals(self):
        created = create_drive_signal("anomaly", "test", "will die", tension=0.05, decay_rate=0.1)
        result = decay_drive_signals()
        assert result["killed"] >= 1
        signal = get_drive_signal(created["id"])
        assert signal is None

    def test_ready_signals_do_not_decay(self):
        created = create_drive_signal("anomaly", "test", "stable ready", tension=0.8)
        update_drive_signal_status(created["id"], "ready")
        # Override status directly for test
        from db import get_db
        get_db().execute("UPDATE drive_signals SET status = 'ready' WHERE id = ?", (created["id"],))
        get_db().commit()
        decay_drive_signals()
        signal = get_drive_signal(created["id"])
        assert signal is not None
        assert float(signal["tension"]) == 0.8


class TestStatusTransitions:
    def test_latent_to_acted(self):
        created = create_drive_signal("opportunity", "test", "cross-sell potential")
        result = update_drive_signal_status(created["id"], "acted", "investigated: 12% uplift possible")
        assert result["ok"]
        signal = get_drive_signal(result["id"])
        assert signal["status"] == "acted"
        assert signal["acted_at"] is not None
        assert signal["outcome"] == "investigated: 12% uplift possible"

    def test_latent_to_dismissed(self):
        created = create_drive_signal("gap", "test", "minor gap")
        result = update_drive_signal_status(created["id"], "dismissed", "not actionable")
        assert result["ok"]
        signal = get_drive_signal(result["id"])
        assert signal["status"] == "dismissed"

    def test_invalid_status_rejected(self):
        created = create_drive_signal("anomaly", "test", "test")
        result = update_drive_signal_status(created["id"], "invalid_status")
        assert not result["ok"]

    def test_nonexistent_signal(self):
        result = update_drive_signal_status(99999, "acted", "test")
        assert not result["ok"]


class TestFindSimilar:
    def test_finds_similar_signal(self):
        create_drive_signal("anomaly", "test", "CPC increased significantly in search campaign")
        similar = find_similar_drive_signal("CPC increased dramatically in search campaign")
        assert similar is not None

    def test_no_match_for_unrelated(self):
        create_drive_signal("anomaly", "test", "CPC increased significantly in search campaign")
        similar = find_similar_drive_signal("The weather is nice today in Mallorca")
        assert similar is None

    def test_area_scoping(self):
        create_drive_signal("anomaly", "test", "metric anomaly detected", area="shopify")
        similar = find_similar_drive_signal("metric anomaly detected", area="google-ads")
        assert similar is None


class TestMaxActiveSignals:
    def test_cap_at_30(self):
        for i in range(32):
            create_drive_signal("anomaly", "test", f"signal number {i}", tension=0.1 + (i * 0.01))
        signals = get_drive_signals(limit=50)
        assert len(signals) <= 30


class TestStats:
    def test_empty_stats(self):
        stats = drive_signal_stats()
        assert stats["total"] == 0

    def test_counts_by_status(self):
        create_drive_signal("anomaly", "test", "one")
        create_drive_signal("pattern", "test", "two")
        s = create_drive_signal("gap", "test", "three")
        update_drive_signal_status(s["id"], "dismissed", "reason")
        stats = drive_signal_stats()
        assert stats["total"] == 3
        assert stats["by_status"].get("latent", 0) == 2
        assert stats["by_status"].get("dismissed", 0) == 1


# ── Detection heuristic tests ────────────────────────────────────────

class TestDetection:
    def test_anomaly_detected(self):
        result = detect_drive_signal(
            "El CPC subió 40% en los últimos 3 días sin cambio de campaña",
            source="heartbeat", source_id="test-sid",
        )
        assert result is not None
        assert result.get("ok") or result.get("new_tension")

    def test_pattern_detected(self):
        result = detect_drive_signal(
            "María pregunta otra vez por el resumen trimestral, siempre pasa lo mismo",
            source="heartbeat", source_id="test-sid",
        )
        assert result is not None

    def test_pattern_detected_in_portuguese(self):
        result = detect_drive_signal(
            "O mesmo problema volta a acontecer sempre que sincronizamos o catalogo",
            source="heartbeat", source_id="test-sid",
        )
        assert result is not None

    def test_gap_detected(self):
        result = detect_drive_signal(
            "No sé cómo funciona el pricing de ICNEA para las agencias",
            source="task_close", source_id="task-123",
        )
        assert result is not None

    def test_gap_detected_in_german(self):
        result = detect_drive_signal(
            "Ich weiss nicht wie dieser Checkout Flow dokumentiert ist y no puedo seguir",
            source="task_close", source_id="task-123-de",
        )
        assert result is not None

    def test_opportunity_detected(self):
        result = detect_drive_signal(
            "El recovery de carritos tiene 12% open rate, el benchmark media del sector está bajo",
            source="task_close", source_id="task-456",
        )
        assert result is not None

    def test_opportunity_detected_in_english(self):
        result = detect_drive_signal(
            "We could automate invoice reconciliation because peers handle this much faster",
            source="task_close", source_id="task-456-en",
        )
        assert result is not None

    def test_anomaly_detected_in_english(self):
        result = detect_drive_signal(
            "Revenue dropped 18% after yesterday deploy and that looks unexpected",
            source="heartbeat", source_id="test-sid-en",
        )
        assert result is not None

    def test_normal_text_ignored(self):
        result = detect_drive_signal(
            "Procesando los emails de hoy, todo normal",
            source="heartbeat", source_id="test-sid",
        )
        assert result is None

    def test_recurrence_without_problem_is_ignored(self):
        result = detect_drive_signal(
            "Otra vez revisé el dashboard y todo sigue bien",
            source="heartbeat", source_id="test-sid-neutral",
        )
        assert result is None

    def test_short_text_ignored(self):
        result = detect_drive_signal("ok", source="heartbeat", source_id="test")
        assert result is None

    def test_area_inferred(self):
        result = detect_drive_signal(
            "El CPC de Google Ads subió 40% inesperadamente",
            source="heartbeat", source_id="test-sid",
        )
        assert result is not None
        signal = get_drive_signals(area="google-ads")
        assert len(signal) >= 1

    def test_reinforces_existing_on_similar(self):
        detect_drive_signal(
            "CPC subió 30% sin cambios en la campaña de search",
            source="heartbeat", source_id="sid1",
        )
        result = detect_drive_signal(
            "CPC subió 25% sin cambios en la campaña de search otra vez",
            source="heartbeat", source_id="sid2",
        )
        # Should reinforce, not create new
        assert result is not None
        signals = get_drive_signals()
        # Should have at most 1 signal for this topic
        cpc_signals = [s for s in signals if "CPC" in s["summary"]]
        assert len(cpc_signals) <= 1


# ── MCP handler tests ────────────────────────────────────────────────

class TestHandlers:
    def test_list_empty(self):
        output = handle_drive_signals()
        assert "No drive signals" in output

    def test_list_with_data(self):
        create_drive_signal("anomaly", "test", "test signal for listing")
        output = handle_drive_signals()
        assert "DRIVE SIGNALS" in output
        assert "test signal" in output

    def test_reinforce_handler(self):
        created = create_drive_signal("anomaly", "test", "reinforce me")
        output = handle_drive_reinforce(created["id"], "new observation")
        assert "reinforced" in output

    def test_reinforce_empty_observation(self):
        output = handle_drive_reinforce(1, "")
        assert "ERROR" in output

    def test_act_handler(self):
        created = create_drive_signal("opportunity", "test", "act on me")
        output = handle_drive_act(created["id"], "found 15% improvement opportunity")
        assert "ACTED" in output

    def test_act_empty_outcome(self):
        output = handle_drive_act(1, "")
        assert "ERROR" in output

    def test_dismiss_handler(self):
        created = create_drive_signal("gap", "test", "dismiss me")
        output = handle_drive_dismiss(created["id"], "not actionable")
        assert "dismissed" in output

    def test_dismiss_empty_reason(self):
        output = handle_drive_dismiss(1, "")
        assert "ERROR" in output
