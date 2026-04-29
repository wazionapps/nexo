import json

from support_snapshot import collect_snapshot


def test_collect_snapshot_returns_generic_runtime_payload(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "runtime" / "data").mkdir(parents=True)
    (home / "runtime" / "logs").mkdir(parents=True)
    (home / "runtime" / "operations").mkdir(parents=True)
    (home / "runtime" / "events.ndjson").write_text(json.dumps({"type": "health_alert", "ts": 1}) + "\n")
    (home / "runtime" / "operations" / "sample.log").write_text("line-a\nline-b\n")
    (home / "version.json").write_text(json.dumps({"version": "7.11.7"}))

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    payload = collect_snapshot(log_lines=10, include_doctor=False)

    assert payload["version"] == "7.11.7"
    assert payload["paths"]["home"]["exists"] is True
    assert "health" in payload
    assert "logs" in payload
    assert isinstance(payload["logs"]["events"], list)
    assert isinstance(payload["logs"]["operations"], list)
