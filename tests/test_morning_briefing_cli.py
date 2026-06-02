from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import db._core as db_core
    import db._schema as db_schema
    import db
    import paths

    db_core.close_db()
    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(paths)
    importlib.reload(db)
    db.init_db()
    yield home
    db_core.close_db()


def test_latest_briefing_persists_bodies_artifacts_and_desktop_marks(isolated_home):
    from morning_briefing import (
        latest_morning_briefing,
        mark_desktop_state,
        mark_morning_briefing_sent,
        write_latest_briefing_artifacts,
    )
    import db

    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO morning_briefing_runs (local_date, recipient, status)
        VALUES ('2026-06-02', 'user@example.com', 'in_progress')
        """
    )
    conn.commit()

    artifact = write_latest_briefing_artifacts(
        recipient="user@example.com",
        subject="Daily briefing",
        body_text="Focus today",
        body_html="<html><body><p>Focus today</p></body></html>",
        local_date="2026-06-02",
    )
    result = mark_morning_briefing_sent(
        local_date="2026-06-02",
        recipient="user@example.com",
        subject="Daily briefing",
        body_text="Focus today",
        body_html="<html><body><p>Focus today</p></body></html>",
        send_output="OK:<id>",
        artifact_payload=artifact,
    )
    briefing = result["briefing"]
    assert briefing["body_text"] == "Focus today"
    assert briefing["unseen"] is True
    assert Path(briefing["artifacts"]["json"]).exists()

    latest = latest_morning_briefing()["briefing"]
    assert latest["subject"] == "Daily briefing"

    marked = mark_desktop_state("shown", briefing_id=latest["id"])
    assert marked["ok"] is True
    assert marked["briefing"]["desktop_shown_at"]
    assert marked["briefing"]["unseen"] is False


def test_morning_briefing_cli_latest_json(isolated_home, capsys):
    from cli import _morning_briefing
    import db
    from morning_briefing import mark_morning_briefing_sent

    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO morning_briefing_runs (local_date, recipient, status)
        VALUES ('2026-06-02', 'user@example.com', 'in_progress')
        """
    )
    conn.commit()
    mark_morning_briefing_sent(
        local_date="2026-06-02",
        recipient="user@example.com",
        subject="Daily briefing",
        body_text="Focus today",
        body_html="<p>Focus today</p>",
    )

    rc = _morning_briefing(SimpleNamespace(morning_briefing_command="latest", include_non_sent=False, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["briefing"]["subject"] == "Daily briefing"
