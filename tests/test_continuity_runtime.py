from __future__ import annotations

import importlib
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def continuity_runtime(tmp_path, monkeypatch, isolated_db):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))
    for name in (
        "paths",
        "db._continuity",
        "db._sessions",
        "db",
        "tools_sessions",
        "continuity",
        "runtime_power",
        "runtime_versioning",
    ):
        sys.modules.pop(name, None)
    import paths
    import db._continuity
    import db._sessions
    import db
    import tools_sessions
    import continuity
    import runtime_versioning

    importlib.reload(paths)
    importlib.reload(db._continuity)
    importlib.reload(db._sessions)
    importlib.reload(db)
    importlib.reload(tools_sessions)
    importlib.reload(continuity)
    importlib.reload(runtime_versioning)
    return {
        "home": home,
        "db": db,
        "continuity": continuity,
        "runtime_versioning": runtime_versioning,
    }


def test_continuity_snapshot_write_is_idempotent_and_reads_by_conversation(continuity_runtime):
    db = continuity_runtime["db"]
    continuity = continuity_runtime["continuity"]

    sid = "nexo-3000-1"
    db.register_session(
        sid,
        "Ship Brain release",
        external_session_id="desktop-ext-1",
        session_client="desktop",
        conversation_id="conv-1",
    )

    first = continuity.write_snapshot(
        conversation_id="conv-1",
        session_id=sid,
        external_session_id="desktop-ext-1",
        client="desktop",
        event_type="turn_end",
        payload={"current_goal": "Ship Brain release", "messages": ["u: hola", "a: sigo"]},
        trace_id="trace-1",
    )
    second = continuity.write_snapshot(
        conversation_id="conv-1",
        session_id=sid,
        external_session_id="desktop-ext-1",
        client="desktop",
        event_type="turn_end",
        payload={"current_goal": "Ship Brain release", "messages": ["u: hola", "a: sigo"]},
        trace_id="trace-1",
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["idempotency_key"] == second["idempotency_key"]

    read = continuity.read_snapshot(conversation_id="conv-1")
    assert read["count"] == 1
    assert read["items"][0]["conversation_id"] == "conv-1"
    assert read["items"][0]["session_id"] == sid


def test_resume_bundle_includes_checkpoint_diary_and_transcript_tail(continuity_runtime):
    db = continuity_runtime["db"]
    continuity = continuity_runtime["continuity"]

    sid = "nexo-3000-2"
    db.register_session(
        sid,
        "Close coordinated release",
        external_session_id="desktop-ext-2",
        session_client="desktop",
        conversation_id="conv-release",
    )
    db.save_checkpoint(
        sid,
        task="Close coordinated release",
        current_goal="Publish Brain and Desktop together",
        next_step="Run release smoke",
        decisions_summary="Ship one coordinated release only",
        errors_found="Old MCP must self-drain after update",
    )
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO session_diary (
            session_id, decisions, discarded, pending, context_next, summary
        ) VALUES (?, ?, '', ?, ?, ?)
        """,
        (
            sid,
            "Desktop must relaunch after Brain update",
            "Build DMG and verify marker clear",
            "Resume from smoke and clear the restart marker.",
            "Resume from smoke then publish the coordinated release.",
        ),
    )
    conn.commit()
    continuity.write_snapshot(
        conversation_id="conv-release",
        session_id=sid,
        external_session_id="desktop-ext-2",
        client="desktop",
        event_type="turn_end",
        payload={
            "current_goal": "Publish Brain and Desktop together",
            "transcript_tail": ["User asked for one coordinated release", "Need continuity + MCP stability"],
        },
        trace_id="trace-release",
    )

    bundle = continuity.build_resume_bundle(
        conversation_id="conv-release",
        session_id=sid,
        external_session_id="desktop-ext-2",
        client="desktop",
    )

    assert bundle["unsafe_sid"] is False
    assert bundle["objective"] == "Publish Brain and Desktop together"
    assert "Run release smoke" in bundle["pending"][0]
    assert "Desktop must relaunch" in bundle["decisions"][0]
    assert "Need continuity + MCP stability" in bundle["resume_text"]


def test_resume_bundle_returns_unsafe_sid_when_conversation_has_two_active_sessions(continuity_runtime):
    db = continuity_runtime["db"]
    continuity = continuity_runtime["continuity"]

    db.register_session(
        "nexo-3000-3",
        "Desktop A",
        external_session_id="ext-a",
        session_client="desktop",
        conversation_id="conv-collision",
    )
    db.register_session(
        "nexo-3000-4",
        "Desktop B",
        external_session_id="ext-b",
        session_client="desktop",
        conversation_id="conv-collision",
    )

    bundle = continuity.build_resume_bundle(
        conversation_id="conv-collision",
        session_id="nexo-3000-3",
        client="desktop",
    )

    assert bundle["ok"] is True
    assert bundle["unsafe_sid"] is True
    assert "conversation_has_multiple_active_sessions" in bundle["reasons"]


def test_runtime_versioning_creates_current_symlink_and_marker(continuity_runtime, monkeypatch):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    core = home / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "cli.py").write_text("print('cli')\n", encoding="utf-8")
    (core / "server.py").write_text("print('server')\n", encoding="utf-8")
    (core / "version.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")

    activated = runtime_versioning.activate_versioned_runtime_snapshot(
        source_root=core,
        version="9.9.9",
    )
    marker = runtime_versioning.write_restart_required_marker(
        from_version="9.9.8",
        to_version="9.9.9",
    )
    runtime_versioning.PROCESS_VERSION = "9.9.8"
    status = runtime_versioning.build_mcp_status(client="claude_code")

    assert activated["ok"] is True
    assert (home / "core" / "current").exists()
    assert (home / "core" / "versions" / "9.9.9" / "cli.py").is_file()
    assert marker["required"] is True
    assert status["restart_required"] is True
    assert status["client_action"] == "restart_session_required"


def test_runtime_versioning_respects_explicit_source_root_over_stale_current(continuity_runtime):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    core = home / "core"
    stale = core / "versions" / "5.3.7"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "server.py").write_text("print('stale')\n", encoding="utf-8")
    (core / "current").symlink_to(Path("versions") / "5.3.7")
    (core / "server.py").write_text("print('fresh')\n", encoding="utf-8")
    (core / "version.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")

    activated = runtime_versioning.activate_versioned_runtime_snapshot(
        source_root=core,
        version="9.9.9",
    )

    assert activated["ok"] is True
    assert activated["source_root"] == str(core)
    assert (home / "core" / "versions" / "9.9.9" / "server.py").read_text(encoding="utf-8") == "print('fresh')\n"


def test_runtime_versioning_prunes_snapshots_older_than_two_back(continuity_runtime):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    versions = home / "core" / "versions"
    for version in ("7.12.12", "7.12.13", "7.12.14", "7.12.15"):
        root = versions / version
        root.mkdir(parents=True, exist_ok=True)
        (root / "server.py").write_text(f"print({version!r})\n", encoding="utf-8")
    (home / "core" / "current").symlink_to(Path("versions") / "7.12.15")

    result = runtime_versioning.prune_old_versioned_runtime_snapshots(
        keep=2,
        active_version="7.12.15",
    )

    assert result["ok"] is True
    assert result["pruned"] == ["7.12.12", "7.12.13"]
    assert (versions / "7.12.14").is_dir()
    assert (versions / "7.12.15").is_dir()
    assert not (versions / "7.12.12").exists()


def test_restart_required_middleware_wraps_string_tool_schema(continuity_runtime, monkeypatch):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    middleware = runtime_versioning.RestartRequiredMiddleware(client="claude_code")
    fake_tool = SimpleNamespace(
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
    )

    async def _get_tool(_name):
        return fake_tool

    context = SimpleNamespace(
        message=SimpleNamespace(name="nexo_memory_save"),
        fastmcp_context=SimpleNamespace(fastmcp=SimpleNamespace(get_tool=_get_tool)),
    )

    monkeypatch.setattr(
        runtime_versioning,
        "resolve_restart_required",
        lambda **_kwargs: {
            "restart_required": True,
            "reason": "marker_required",
            "client_action": "restart_session_required",
            "installed_version": "7.9.6",
            "process_version": "7.9.6",
            "marker": {"required": True},
        },
    )

    result = asyncio.run(middleware.on_call_tool(context, lambda _ctx: None))

    assert result.structured_content == {
        "result": json.dumps(
            {
                "ok": False,
                "error": "mcp_restart_required",
                "message": "NEXO Brain was updated. Restart this MCP client/session.",
                "restart_required": True,
                "tool": "nexo_memory_save",
                "installed_version": "7.9.6",
                "process_version": "7.9.6",
                "reason": "marker_required",
                "client_action": "restart_session_required",
            },
            ensure_ascii=False,
        )
    }
    assert "mcp_restart_required" in result.content[0].text


def test_clear_restart_marker_waits_until_all_clients_ack(continuity_runtime):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    config_dir = home / "personal" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "schedule.json").write_text(
        json.dumps(
            {
                "interactive_clients": {
                    "claude_desktop": True,
                    "claude_code": True,
                    "codex": True,
                }
            }
        ),
        encoding="utf-8",
    )

    runtime_versioning.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.1.0",
    )
    desktop = runtime_versioning.clear_restart_required_marker(
        client="claude_desktop",
        installed_version="1.1.0",
        process_version="1.1.0",
    )
    claude = runtime_versioning.clear_restart_required_marker(
        client="claude_code",
        installed_version="1.1.0",
        process_version="1.1.0",
    )
    codex = runtime_versioning.clear_restart_required_marker(
        client="codex",
        installed_version="1.1.0",
        process_version="1.1.0",
    )

    assert desktop["cleared"] is False
    assert claude["cleared"] is False
    assert codex["cleared"] is True
    assert not runtime_versioning.restart_required_marker_path().exists()


def test_restart_marker_uses_enabled_interactive_clients_only(continuity_runtime):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    config_dir = home / "personal" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "schedule.json").write_text(
        json.dumps(
            {
                "interactive_clients": {
                    "claude_code": True,
                    "codex": False,
                    "claude_desktop": False,
                }
            }
        ),
        encoding="utf-8",
    )

    marker = runtime_versioning.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.1.0",
    )
    cleared = runtime_versioning.clear_restart_required_marker(
        client="claude_code",
        installed_version="1.1.0",
        process_version="1.1.0",
    )

    assert marker["clients"] == {"claude_code": "restart_session_required"}
    assert cleared["cleared"] is True
    assert not runtime_versioning.restart_required_marker_path().exists()


def test_restart_middleware_auto_acks_current_client_when_versions_match(continuity_runtime):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    home = continuity_runtime["home"]
    home.mkdir(parents=True, exist_ok=True)
    (home / "version.json").write_text(json.dumps({"version": "1.1.0"}), encoding="utf-8")
    runtime_versioning.PROCESS_VERSION = "1.1.0"
    runtime_versioning.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.1.0",
        client="claude_code",
    )
    middleware = runtime_versioning.RestartRequiredMiddleware(client="claude_code")
    context = SimpleNamespace(message=SimpleNamespace(name="nexo_memory_save"))

    async def _next(_context):
        return {"ok": True}

    result = asyncio.run(middleware.on_call_tool(context, _next))

    assert result == {"ok": True}
    assert not runtime_versioning.restart_required_marker_path().exists()


def test_startup_is_allowed_during_restart_marker(continuity_runtime, monkeypatch):
    runtime_versioning = continuity_runtime["runtime_versioning"]
    middleware = runtime_versioning.RestartRequiredMiddleware(client="")
    context = SimpleNamespace(message=SimpleNamespace(name="nexo_startup"))
    monkeypatch.setattr(
        runtime_versioning,
        "resolve_restart_required",
        lambda **_kwargs: {
            "restart_required": True,
            "reason": "marker_required",
            "client_action": "",
            "installed_version": "7.9.15",
            "process_version": "7.9.15",
            "marker": {"required": True},
        },
    )

    async def _next(_context):
        return {"ok": True}

    assert asyncio.run(middleware.on_call_tool(context, _next)) == {"ok": True}
