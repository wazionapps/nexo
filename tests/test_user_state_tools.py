"""Tests for richer user-state modeling."""


def test_user_state_prefers_urgent_when_urgency_signals_exist():
    import cognitive
    import user_state_model
    from db._hot_context import capture_context_event

    cognitive.log_sentiment("esto es urgente, hazlo ya")
    capture_context_event(
        event_type="blocker",
        title="Release blocked",
        summary="Waiting on a release blocker",
        context_key="workflow:release",
        context_title="Release blocked",
        context_summary="Waiting on a release blocker",
        context_type="workflow",
        state="blocked",
        source_type="workflow",
        source_id="WF-1",
        session_id="nexo-test",
    )

    snapshot = user_state_model.build_user_state(days=7, persist=True)
    assert snapshot["state_label"] == "urgent"


def test_user_state_snapshots_are_persisted():
    import user_state_model

    user_state_model.build_user_state(days=7, persist=True)
    history = user_state_model.list_user_state_snapshots(limit=5)
    assert history
    assert "state_label" in history[0]
