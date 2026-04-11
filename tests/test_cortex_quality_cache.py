"""Tests for the v5.2.0 cortex-quality cache reader.

The cortex-cycle cron writes a snapshot every 6h to
$NEXO_HOME/operations/cortex-quality-latest.json. Before v5.2.0 the snapshot
was write-only — nexo_cortex_quality recomputed from the DB on every call.
These tests lock in the read path so regressions are caught immediately.

The key constraint: on ANY cache failure (missing file, corrupt JSON, stale
timestamp, unknown schema, window not cached), the handler must fall back
silently to the live cortex_evaluation_summary computation. The cache is a
performance optimisation, never a correctness dependency.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def _write_cache(nexo_home: Path, payload: dict) -> Path:
    operations_dir = nexo_home / "operations"
    operations_dir.mkdir(parents=True, exist_ok=True)
    cache_file = operations_dir / "cortex-quality-latest.json"
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return cache_file


def _fresh_payload(window_7d: dict | None = None, window_1d: dict | None = None) -> dict:
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "window_7d": window_7d or {"total_evaluations": 42, "accept_rate": 87.5},
        "window_1d": window_1d or {"total_evaluations": 7, "accept_rate": 85.0},
        "signals": [],
        "schema": 1,
    }


@pytest.fixture
def cortex_with_home(tmp_path, monkeypatch):
    """Point cortex at a temp NEXO_HOME and return the freshly-reloaded module."""
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import plugins.cortex as cortex
    importlib.reload(cortex)
    yield cortex, tmp_path


def test_cache_hit_serves_fresh_7d_window_without_touching_db(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    expected_window = {
        "total_evaluations": 42,
        "accept_rate": 87.5,
        "recommended_success_rate": 91.3,
    }
    _write_cache(home, _fresh_payload(window_7d=expected_window))

    # If the cache path works, cortex_evaluation_summary must NEVER be called.
    # We sabotage it on purpose so any fall-through crashes the test loudly.
    def _exploding_summary(**_kwargs):
        raise AssertionError("live path should not run when cache is fresh")

    import db
    monkeypatch.setattr(db, "cortex_evaluation_summary", _exploding_summary)

    raw = cortex.handle_cortex_quality(days=7)
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["source"] == "cache"
    assert payload["summary"] == expected_window


def test_cache_hit_serves_fresh_1d_window(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    expected_window = {"total_evaluations": 7, "accept_rate": 82.0}
    _write_cache(home, _fresh_payload(window_1d=expected_window))

    import db
    monkeypatch.setattr(
        db,
        "cortex_evaluation_summary",
        lambda **_kwargs: pytest.fail("should not reach live path"),
    )

    payload = json.loads(cortex.handle_cortex_quality(days=1))
    assert payload["source"] == "cache"
    assert payload["summary"] == expected_window


def test_stale_cache_falls_back_to_live_summary(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    stale_payload = _fresh_payload()
    # Push captured_at 25000 seconds into the past — well beyond the
    # 23400s (6h30m) limit.
    stale_at = datetime.now() - timedelta(seconds=25000)
    stale_payload["captured_at"] = stale_at.isoformat(timespec="seconds")
    _write_cache(home, stale_payload)

    calls: list[int] = []

    def _fake_summary(**kwargs):
        calls.append(kwargs.get("days", -1))
        return {"total_evaluations": 1, "accept_rate": 0.0, "_source_marker": "live"}

    import db
    monkeypatch.setattr(db, "cortex_evaluation_summary", _fake_summary)

    payload = json.loads(cortex.handle_cortex_quality(days=7))
    assert payload["source"] == "live"
    assert payload["summary"]["_source_marker"] == "live"
    assert calls == [7]


def test_corrupt_schema_falls_back_to_live(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    corrupt = _fresh_payload()
    corrupt["schema"] = 99  # unknown schema version
    _write_cache(home, corrupt)

    def _fake_summary(**_kwargs):
        return {"total_evaluations": 0, "accept_rate": 0.0, "_from": "live"}

    import db
    monkeypatch.setattr(db, "cortex_evaluation_summary", _fake_summary)

    payload = json.loads(cortex.handle_cortex_quality(days=7))
    assert payload["source"] == "live"
    assert payload["summary"]["_from"] == "live"


def test_invalid_json_file_falls_back_to_live(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    operations_dir = home / "operations"
    operations_dir.mkdir(parents=True, exist_ok=True)
    (operations_dir / "cortex-quality-latest.json").write_text(
        "{not valid json", encoding="utf-8"
    )

    import db
    monkeypatch.setattr(
        db,
        "cortex_evaluation_summary",
        lambda **_kwargs: {"live": True},
    )

    payload = json.loads(cortex.handle_cortex_quality(days=7))
    assert payload["source"] == "live"
    assert payload["summary"] == {"live": True}


def test_missing_cache_file_falls_back_to_live(cortex_with_home, monkeypatch):
    cortex, home = cortex_with_home
    # Do NOT write any cache file.

    import db
    monkeypatch.setattr(
        db,
        "cortex_evaluation_summary",
        lambda **_kwargs: {"no_cache": True},
    )

    payload = json.loads(cortex.handle_cortex_quality(days=7))
    assert payload["source"] == "live"


def test_non_cached_window_30d_always_uses_live(cortex_with_home, monkeypatch):
    """30-day window is never in the cron snapshot — must route to live."""
    cortex, home = cortex_with_home
    _write_cache(home, _fresh_payload())  # fresh 7d/1d cache present

    def _fake_summary(**kwargs):
        return {"days_requested": kwargs.get("days"), "_from": "live"}

    import db
    monkeypatch.setattr(db, "cortex_evaluation_summary", _fake_summary)

    payload = json.loads(cortex.handle_cortex_quality(days=30))
    assert payload["source"] == "live"
    assert payload["summary"]["days_requested"] == 30
