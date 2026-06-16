import time


def test_stop_all_keepalives_joins_background_threads(monkeypatch):
    import tools_sessions

    calls = []

    def fake_update_session(sid, task):
        calls.append((sid, task))

    monkeypatch.setattr(tools_sessions, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(tools_sessions, "update_session", fake_update_session)

    sid = "nexo-test-keepalive"
    tools_sessions._start_keepalive(sid)
    stop_event, thread = tools_sessions._keepalive_threads[sid]
    assert not stop_event.is_set()

    deadline = time.monotonic() + 1.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)

    tools_sessions._stop_all_keepalives(join_timeout=1.0)

    assert tools_sessions._keepalive_threads == {}
    assert stop_event.is_set()
    assert not thread.is_alive()
    assert calls
    assert all(call == (sid, None) for call in calls)
