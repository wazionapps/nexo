"""Brain recall time_range fix (operator session ff78ff94, 11-jun).

Repro: nexo_memory_timeline/search with time_range="2026-06-04..2026-06-05"
returned the most RECENT events instead of that window. Two chained root
causes: (1) _parse_time_range only understood relative values (today/ayer/
last N) and silently disabled the filter for everything else; (2) even a
parsed window was applied in Python AFTER a recency-truncated fetch, so old
windows never reached the candidate set.

Contract pinned here: absolute ISO dates, ISO ranges, ISO datetimes, epoch
and epoch ranges all parse; and the SQL fetch honours start/end so old rows
surface for old windows.
"""

import importlib
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import db._core as core
    import db._memory_v2 as mem
    import memory_retrieval as retrieval
    importlib.reload(core)
    importlib.reload(mem)
    importlib.reload(retrieval)
    yield mem, retrieval
    importlib.reload(core)


def _ts(date_text):
    return datetime.fromisoformat(date_text).timestamp()


def test_parse_time_range_absolute_formats(env):
    _mem, retrieval = env
    parse = retrieval._parse_time_range

    start, end, label = parse("2026-06-04")
    assert label and start is not None and end is not None
    assert datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M") == "2026-06-04 00:00"
    assert end - start == pytest.approx(86400, abs=2)

    start, end, _ = parse("2026-06-04..2026-06-05")
    assert datetime.fromtimestamp(start).strftime("%Y-%m-%d") == "2026-06-04"
    # End date is inclusive as a day -> exclusive bound is the NEXT midnight.
    assert datetime.fromtimestamp(end).strftime("%Y-%m-%d") == "2026-06-06"

    start, end, _ = parse("2026-06-04T10:30:00..2026-06-04T11:00:00")
    assert end - start == pytest.approx(1800, abs=2)

    now = time.time()
    start, end, _ = parse(f"{int(now - 3600)}..{int(now)}")
    assert start == pytest.approx(now - 3600, abs=2)
    assert end == pytest.approx(now, abs=2)

    # Relative values keep working.
    start, end, label = parse("hoy")
    assert label == "today" and start is not None

    # Garbage still disables the filter (back-compat).
    assert retrieval._parse_time_range("whenever") == (None, None, "")


def test_old_window_rows_surface_despite_recency_truncation(env):
    mem, retrieval = env
    conn_mod = mem

    old_day = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_ts = _ts(old_day) + 3600

    # One OLD event (created_at honoured by the recorder)...
    conn_mod.record_memory_event(
        event_type="task", source_type="test", source_id="old-window-probe",
        session_id="s-old", created_at=old_ts,
    )
    # ...plus enough recent events to flood any recency-truncated fetch.
    for i in range(80):
        conn_mod.record_memory_event(
            event_type="task", source_type="test", source_id=f"recent-filler-{i}",
            session_id="s-new",
        )

    rows = conn_mod.list_memory_events(limit=10, start_ts=_ts(old_day), end_ts=_ts(old_day) + 86400)
    ids = [r.get("source_id") for r in rows]
    assert "old-window-probe" in ids, f"old-window row must surface via SQL bounds, got {ids}"
    assert all("filler" not in str(s) for s in ids), "rows outside the window must be excluded in SQL"
