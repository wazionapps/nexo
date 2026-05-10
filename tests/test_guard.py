"""Tests for guard conditioned file learnings."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_guard_stack():
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._learnings as db_learnings
    import db
    import plugins.guard as guard

    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_learnings)
    importlib.reload(db)
    importlib.reload(guard)
    return db, guard


@pytest.fixture
def guard_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


def test_handle_guard_file_check_surfaces_conditioned_learning(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    created = db.create_learning(
        "nexo-ops",
        "Read protocol rules before editing",
        "Protocol changes require reading the active rule first.",
        prevention="Read the conditioned learning before touching the file.",
        applies_to="/repo/src/plugins/protocol.py",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 0.9 WHERE id = ?",
        (created["id"],),
    )
    conn.commit()

    output = guard.handle_guard_file_check(["/repo/src/plugins/protocol.py"])

    assert "WARNINGS — resolve before editing:" in output
    assert "conditioned learning" in output
    assert "Read protocol rules before editing" in output


def test_handle_guard_check_promotes_conditioned_learning_to_blocking_rule(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    created = db.create_learning(
        "nexo-ops",
        "Never edit migration history blindly",
        "Never edit migration history blindly; read the conditioned rule first.",
        prevention="Review the learning before editing schema files.",
        applies_to="/repo/src/db/_schema.py",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 1.0 WHERE id = ?",
        (created["id"],),
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/src/db/_schema.py", area="nexo")

    assert "BLOCKING RULES" in output
    assert "FILE RULE:/repo/src/db/_schema.py" in output
    assert "Never edit migration history blindly" in output


def test_handle_guard_check_does_not_promote_file_scoped_rules_to_universal_rules(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    db.create_learning(
        "nexo-ops",
        "Never edit guard.py directly",
        "Never edit guard.py directly; route all fixes through wrapper helpers instead.",
        prevention="Use the conditioned hotfix path instead.",
        applies_to="/repo/src/plugins/guard.py",
        status="active",
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/src/doctor/providers/runtime.py", area="nexo")

    assert "UNIVERSAL RULES" not in output
    assert "Never edit guard.py directly" not in output


def test_handle_guard_check_does_not_pull_unrelated_nexo_ops_universal_rules(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    db.create_learning(
        "nexo-ops",
        "Cloudflare access must be verified before diagnosing Wrangler auth",
        "SIEMPRE verify Cloudflare access before claiming Wrangler auth is the root cause.",
        status="active",
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/src/doctor/providers/runtime.py", area="nexo")

    assert "UNIVERSAL RULES" not in output
    assert "Cloudflare access must be verified before diagnosing Wrangler auth" not in output


def test_handle_guard_check_blocks_installed_runtime_core_paths(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    runtime_core_file = guard_env / "core" / "plugins" / "protocol.py"
    runtime_core_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_core_file.write_text("# installed runtime core\n", encoding="utf-8")

    output = guard.handle_guard_check(files=str(runtime_core_file), area="nexo-ops")

    assert "BLOCKING RULES" in output
    assert f"FILE RULE:{runtime_core_file}" in output
    assert "Installed runtime core files are protected" in output


def test_handle_guard_check_skips_runtime_core_when_caller_opts_out(guard_env):
    """The pre-emptive runner guard opts out of the runtime-core block via
    ``enforce_runtime_core_block='false'`` because the PreToolUse hook
    (``hook_guardrails._collect_runtime_core_write_blocks``) already blocks
    actual writes on those paths with severity ``error``. Without this opt
    out, every email-monitor/followup-runner/morning-agent session aborted
    pre-emptively because their prompts mention runtime-core helper script
    paths (e.g. ``python3 ~/.nexo/core/scripts/nexo-send-reply.py ...``)
    which the path extractor cannot easily distinguish from edits.
    """
    db, guard = _reload_guard_stack()
    db.init_db()

    runtime_core_file = guard_env / "core" / "scripts" / "nexo-send-reply.py"
    runtime_core_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_core_file.write_text("# installed runtime core helper\n", encoding="utf-8")

    output = guard.handle_guard_check(
        files=str(runtime_core_file),
        area="runner:email-monitor",
        enforce_runtime_core_block="false",
    )

    assert "BLOCKING RULES" not in output, (
        "with enforce_runtime_core_block='false' the runtime-core path must NOT trigger a blocking rule"
    )
    assert "Installed runtime core files are protected" not in output


def test_handle_guard_check_default_still_blocks_runtime_core(guard_env):
    """Default callers (no opt-out) keep the historic behaviour: a runtime-core
    path raises the BLOCKING RULES banner. This pins the back-compat contract
    so the new opt-out parameter cannot regress regular ``nexo_guard_check``
    invocations from agents."""
    db, guard = _reload_guard_stack()
    db.init_db()

    runtime_core_file = guard_env / "core" / "scripts" / "nexo-send-reply.py"
    runtime_core_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_core_file.write_text("# installed runtime core helper\n", encoding="utf-8")

    output = guard.handle_guard_check(files=str(runtime_core_file), area="nexo-ops")

    assert "BLOCKING RULES" in output
    assert "Installed runtime core files are protected" in output


def test_handle_guard_check_does_not_match_generic_parent_directory_tokens(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    db.create_learning(
        "nexo-ops",
        "Never touch generic scripts blindly",
        "Never touch scripts blindly; always re-check launchd metadata first.",
        status="active",
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/personal/scripts/new-automation.py", area="personal-scripts")

    assert "Never touch generic scripts blindly" not in output


def test_handle_guard_check_project_hint_filters_unrelated_blocking_rules(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    matching = db.create_learning(
        "shopify",
        "Never deploy recambios-bmw theme blindly",
        "NEVER deploy recambios-bmw theme blindly; verify theme id and backup first.",
        status="active",
    )
    unrelated = db.create_learning(
        "shopify",
        "Never deploy wazion storefront blindly",
        "NEVER deploy wazion storefront blindly; verify live storefront status first.",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 1.0 WHERE id IN (?, ?)",
        (matching["id"], unrelated["id"]),
    )
    conn.commit()

    output = guard.handle_guard_check(
        files="/repo/shopify/theme/snippets/reviews.liquid",
        area="shopify",
        project_hint="recambios-bmw",
    )

    assert "Never deploy recambios-bmw theme blindly" in output
    assert "Never deploy wazion storefront blindly" not in output


def test_handle_guard_check_skips_cross_area_filename_matches_without_exact_path_or_project_hint(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    db.create_learning(
        "shopify",
        "Do not edit settings_data.json blindly",
        "Never edit settings_data.json blindly; verify the storefront backup first.",
        status="active",
    )
    conn.commit()

    output = guard.handle_guard_check(
        files="/repo/personal/scripts/settings_data.json",
        area="personal-scripts",
    )

    assert "Do not edit settings_data.json blindly" not in output


def test_handle_guard_check_collapses_duplicate_blocking_rules_with_same_title(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    first = db.create_learning(
        "shopify",
        "Separate product Brain from personal instance",
        "NEVER mix product Brain changes with a personal live instance.",
        status="active",
    )
    second = db.create_learning(
        "shopify",
        "Separate product Brain from personal instance",
        "NEVER mix product Brain changes with a personal live instance.",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 1.0 WHERE id IN (?, ?)",
        (first["id"], second["id"]),
    )
    conn.commit()

    output = guard.handle_guard_check(
        files="/repo/shopify/theme/snippets/reviews.liquid",
        area="shopify",
    )

    assert output.count("Separate product Brain from personal instance") == 1


def test_handle_guard_file_check_skips_file_scoped_rules_for_other_files(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    db.create_learning(
        "nexo-ops",
        "Never edit guard.py directly",
        "Never edit guard.py directly; route all fixes through wrapper helpers instead.",
        applies_to="/repo/src/plugins/guard.py",
        status="active",
    )

    output = guard.handle_guard_file_check(["/repo/src/doctor/providers/runtime.py"])

    assert "Never edit guard.py directly" not in output


# ---------------------------------------------------------------------------
# v6.0.3 — guard_checks.session_id must carry the caller's SID
# ---------------------------------------------------------------------------


def _seed_session(conn, sid: str, *, ext: str = "", claude_sid: str = "",
                  last_update: float | None = None) -> None:
    import time as _time
    ts = last_update if last_update is not None else _time.time()
    conn.execute(
        "INSERT INTO sessions (sid, task, started_epoch, last_update_epoch, "
        "external_session_id, claude_session_id) "
        "VALUES (?, 'test', ?, ?, ?, ?)",
        (sid, ts, ts, ext, claude_sid),
    )
    conn.commit()


def test_guard_check_persists_active_sid_from_env(guard_env, monkeypatch):
    db, guard = _reload_guard_stack()
    db.init_db()

    sid = "nexo-1700000000-11111"
    conn = db.get_db()
    _seed_session(conn, sid)
    monkeypatch.setenv("NEXO_SID", sid)

    guard.handle_guard_check(files="/repo/src/any.py", area="nexo")

    row = conn.execute(
        "SELECT session_id FROM guard_checks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["session_id"] == sid, (
        "regression of v6.0.2 bug — guard_checks.session_id must carry the "
        "caller's SID so missing_file_guard can see the call"
    )


def test_guard_check_resolves_sid_via_external_session_id(guard_env, monkeypatch):
    db, guard = _reload_guard_stack()
    db.init_db()

    sid = "nexo-1700000000-22222"
    claude_sid = "cb7e03a2-aaaa-bbbb-cccc-dddddddddddd"
    conn = db.get_db()
    _seed_session(conn, sid, ext=claude_sid, claude_sid=claude_sid)
    monkeypatch.delenv("NEXO_SID", raising=False)
    monkeypatch.setenv("CLAUDE_SESSION_ID", claude_sid)

    guard.handle_guard_check(files="/repo/src/any.py", area="nexo")

    row = conn.execute(
        "SELECT session_id FROM guard_checks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["session_id"] == sid


def test_guard_check_falls_back_to_most_recent_session(guard_env, monkeypatch):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    _seed_session(conn, "nexo-1700000000-33333", last_update=1_700_000_000.0)
    _seed_session(conn, "nexo-1700000500-44444", last_update=1_700_000_500.0)
    monkeypatch.delenv("NEXO_SID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

    guard.handle_guard_check(files="/repo/src/any.py", area="nexo")

    row = conn.execute(
        "SELECT session_id FROM guard_checks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["session_id"] == "nexo-1700000500-44444"


def test_guard_check_inserts_empty_sid_only_when_no_sessions_exist(guard_env, monkeypatch):
    db, guard = _reload_guard_stack()
    db.init_db()
    monkeypatch.delenv("NEXO_SID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

    conn = db.get_db()
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    guard.handle_guard_check(files="/repo/src/any.py", area="nexo")

    row = conn.execute(
        "SELECT session_id FROM guard_checks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    # With no sessions to resolve, falling back to '' is the safe outcome —
    # the guard check still completes and the caller sees learnings, but
    # hook_guardrails will treat it as "no guard seen" (that's the right
    # signal when no one is actually tracking the caller).
    assert row["session_id"] == ""
