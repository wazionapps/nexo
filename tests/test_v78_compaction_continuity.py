"""v7.8 compaction continuity tests.

Francisco flagged ten concrete requirements for PostCompact closure
after v7.7. This file pins each invariant:

  1. PostCompact is registered as a real hook in manifest + runtime.
  2. pre-compact.sh uses the EXACT CLAUDE_SESSION_ID (not LATEST_SID).
  3. post-compact.sh fail-closes instead of falling back to latest.
  4. The engine consumes pending hook events so pre_/post_compaction
     on_event rules fire from the live stream (not just tests).
  5. Multi-conv behaviour: sidecar state lives under NEXO_HOME so two
     concurrent compactions do not clobber each other.
  6. compaction_count is incremented only on successful restore.
  7. Install target files exist.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SRC = REPO_ROOT / "src" / "hooks" / "manifest.json"
HOOKS_RUNTIME = REPO_ROOT / "hooks" / "hooks.json"
PRE_COMPACT_SH = REPO_ROOT / "src" / "hooks" / "pre-compact.sh"
POST_COMPACT_SH = REPO_ROOT / "src" / "hooks" / "post-compact.sh"
POST_COMPACT_PY = REPO_ROOT / "src" / "hooks" / "post_compact.py"
ENGINE = REPO_ROOT / "src" / "enforcement_engine.py"


def _manifest() -> list[dict]:
    return json.loads(MANIFEST_SRC.read_text(encoding="utf-8"))["hooks"]


def _runtime_hooks() -> dict:
    return json.loads(HOOKS_RUNTIME.read_text(encoding="utf-8"))["hooks"]


def test_postcompact_registered_in_src_manifest():
    """Rail 1: src/hooks/manifest.json must carry a PostCompact entry
    marked critical. Without it, the runtime sync never installs the
    hook into the operator's Claude Code settings."""
    hooks = _manifest()
    post = [h for h in hooks if h.get("event") == "PostCompact"]
    assert post, "src/hooks/manifest.json must register PostCompact"
    assert post[0].get("handler", "").endswith("post_compact.py"), (
        "PostCompact handler must be src/hooks/post_compact.py"
    )
    assert post[0].get("critical") is True, (
        "PostCompact must be marked critical so doctor's hooks parity "
        "test flags a missing registration."
    )


def test_postcompact_registered_in_runtime_hooks_json():
    """Rail 1 part 2: hooks/hooks.json (the file Claude Code reads via
    the plugin install flow) must also have the PostCompact block,
    otherwise installs that bypass the sync still miss the hook."""
    runtime = _runtime_hooks()
    assert "PostCompact" in runtime, (
        "hooks/hooks.json must declare a PostCompact hook block."
    )
    entries = runtime["PostCompact"]
    assert entries and isinstance(entries, list), (
        "PostCompact block must contain at least one matcher group."
    )
    inner = entries[0].get("hooks", [])
    assert any("post_compact.py" in (h.get("command", "") or "") for h in inner), (
        "PostCompact runtime block must invoke post_compact.py."
    )


def test_post_compact_py_wrapper_exists_and_proxies_stdout():
    """Rail 1 part 3: the Python wrapper must exist and MUST proxy
    post-compact.sh stdout (so Claude Code actually receives the
    systemMessage JSON). A silent wrapper would kill the UX."""
    assert POST_COMPACT_PY.is_file(), "post_compact.py wrapper missing"
    src = POST_COMPACT_PY.read_text(encoding="utf-8")
    assert "post-compact.sh" in src, "wrapper must delegate to the shell script"
    assert "sys.stdout.write" in src, (
        "wrapper MUST proxy shell stdout or Claude Code loses the "
        "systemMessage emitted by post-compact.sh."
    )


def test_pre_compact_uses_claude_session_id_not_latest():
    """Rail 2: pre-compact.sh must resolve the session via
    CLAUDE_SESSION_ID (the token Claude Code passes to every hook)
    and NOT fall back to LATEST_SID for multi-conv safety."""
    src = PRE_COMPACT_SH.read_text(encoding="utf-8")
    assert "CLAUDE_SESSION_ID" in src, (
        "pre-compact.sh must use CLAUDE_SESSION_ID env (Claude Code hook token)."
    )
    # The old "LATEST_SID" pattern must be GONE from executable code.
    # The explanatory comment that mentions it for clarity is fine, but
    # no variable assignment or substitution may remain.
    assert "LATEST_SID=" not in src, (
        "pre-compact.sh must not assign LATEST_SID — multi-conv broke it."
    )
    assert "$LATEST_SID" not in src, (
        "pre-compact.sh must not reference $LATEST_SID anywhere executable."
    )
    # Sidecar moves to NEXO_HOME, not /tmp, so two conversations don't
    # clobber each other.
    assert "/tmp/nexo-compacting-sid" not in src, (
        "pre-compact.sh must not use /tmp for the sidecar — NEXO_HOME "
        "is the correct scope for multi-conv concurrency."
    )
    assert "compacting-sid.txt" in src, (
        "pre-compact.sh must write the NEXO_HOME-scoped sidecar for PostCompact."
    )


def test_post_compact_fails_closed_without_exact_sid():
    """Rail 3: post-compact.sh must NOT fallback to the latest checkpoint
    when the exact SID is missing — restoring another conversation is
    worse than restoring nothing."""
    src = POST_COMPACT_SH.read_text(encoding="utf-8")
    # The old fallback was:
    #   if [ -z "$CHECKPOINT" ]; then
    #     CHECKPOINT=$(sqlite3 ... ORDER BY updated_at DESC LIMIT 1)
    #   fi
    # That block must be gone.
    assert "ORDER BY updated_at DESC LIMIT 1" not in src, (
        "post-compact.sh must NOT fall back to 'latest checkpoint'. "
        "Fail-closed to avoid cross-conv leaks."
    )
    # Sidecar source must be NEXO_HOME, not /tmp (mirrors pre-compact).
    assert "/tmp/nexo-compacting-sid" not in src, (
        "post-compact.sh must not read the sidecar from /tmp — NEXO_HOME "
        "is the correct scope."
    )
    assert "compacting-sid.txt" in src, (
        "post-compact.sh must read the NEXO_HOME-scoped sidecar."
    )


def test_post_compact_cross_checks_claude_session_id_on_mismatch():
    """Rail 3 part 2: if the pre-compact sidecar SID and the env-resolved
    SID disagree, the hook must refuse to restore and surface a
    diagnostic. Otherwise cross-conv leaks are possible."""
    src = POST_COMPACT_SH.read_text(encoding="utf-8")
    assert "SID mismatch" in src, (
        "post-compact.sh must emit a 'SID mismatch' diagnostic when the "
        "sidecar and env-resolved SIDs disagree (multi-conv safety)."
    )


def test_both_hooks_emit_pending_events():
    """Rail 4: pre-compact.sh and post-compact.sh must append events to
    pending_enforcer_events.ndjson so the engine's live stream dispatcher
    fires the map's pre_compaction / post_compaction on_event rules."""
    pre = PRE_COMPACT_SH.read_text(encoding="utf-8")
    post = POST_COMPACT_SH.read_text(encoding="utf-8")
    assert "pending_enforcer_events.ndjson" in pre, (
        "pre-compact.sh must enqueue a pre_compaction event for the engine."
    )
    assert "'event': 'pre_compaction'" in pre, (
        "pre-compact.sh must emit event=pre_compaction."
    )
    assert "pending_enforcer_events.ndjson" in post, (
        "post-compact.sh must enqueue a post_compaction event for the engine."
    )
    assert "'event': 'post_compaction'" in post, (
        "post-compact.sh must emit event=post_compaction."
    )


def test_engine_consumes_pending_events_on_periodic_tick():
    """Rail 4 part 2: the engine must drain the pending events file on
    every periodic tick. Without the consumer, the hooks' events never
    reach raise_event."""
    src = ENGINE.read_text(encoding="utf-8")
    assert "_consume_pending_hook_events" in src, (
        "EnforcementEngine must implement _consume_pending_hook_events."
    )
    # check_periodic must invoke the consumer — otherwise the hook
    # events never reach the live engine and v7.8 fails its own contract.
    idx = src.find("def check_periodic(self)")
    assert idx != -1, "check_periodic must exist on the engine"
    window = src[idx:idx + 4000]
    assert "_consume_pending_hook_events" in window, (
        "check_periodic must invoke _consume_pending_hook_events on every tick."
    )


def test_engine_consumer_rewrites_queue_without_mine():
    """Rail 4 part 3 (v7.8.1 update): consumed rows must not fire
    twice, but other sessions' rows must survive. The v7.8.0 code
    truncated the whole file; v7.8.1 rewrites the file with the rows
    this session did NOT claim (`keep_raw`) so cross-session events
    persist for the engine that owns them."""
    src = ENGINE.read_text(encoding="utf-8")
    idx = src.find("def _consume_pending_hook_events(self)")
    assert idx != -1
    window = src[idx:idx + 5000]
    # The consumer MUST open the queue for write and MUST write
    # `keep_raw` back — not truncate unconditionally.
    assert 'open(queue_path, "w"' in window, (
        "consumer must reopen the queue for write to drop its own rows."
    )
    assert "keep_raw" in window, (
        "consumer must preserve other sessions' rows in `keep_raw` when "
        "rewriting the queue (v7.8.1 cross-session fix)."
    )


def test_post_compact_handler_records_hook_runs_with_session_id():
    """Rail 6: hook_runs rows must carry the real session_id for
    audit. post_compact.py must pass CLAUDE_SESSION_ID to record_hook_run."""
    src = POST_COMPACT_PY.read_text(encoding="utf-8")
    assert "record_hook_run" in src, "post_compact.py must record via hook_observability"
    assert "CLAUDE_SESSION_ID" in src, (
        "post_compact.py must propagate the Claude session id so hook_runs "
        "is auditable per-conversation."
    )


def test_compaction_count_is_incremented_only_on_real_restore():
    """Rail 8: compaction_count must not tick on every save_checkpoint
    MCP call; it must tick only when post-compact.sh actually restores a
    checkpoint. This is the existing behaviour — the test pins it so a
    future refactor cannot accidentally conflate the two."""
    src = POST_COMPACT_SH.read_text(encoding="utf-8")
    # The UPDATE statement that bumps compaction_count must live inside
    # the CHECKPOINT-found branch. We verify by finding the nearest
    # containing `if [ -n "$CHECKPOINT" ]` guard BEFORE the UPDATE.
    upd_idx = src.find("SET compaction_count = compaction_count + 1")
    assert upd_idx != -1, "post-compact.sh must bump compaction_count on restore"
    head = src[:upd_idx]
    last_if_checkpoint = head.rfind('if [ -n "$CHECKPOINT" ]')
    assert last_if_checkpoint != -1, (
        "compaction_count++ must live under a CHECKPOINT-found guard."
    )
    # Must not be an empty-checkpoint fallback block.
    last_empty_fallback = head.rfind('if [ -z "$CHECKPOINT" ]')
    assert last_empty_fallback < last_if_checkpoint, (
        "compaction_count++ must live inside the real-restore branch of "
        "post-compact.sh (not the no-checkpoint fallback)."
    )


# ── v7.8.1 — per-session queue + per-session sidecar (behavioural) ────


def _build_engine_for_session(sid: str):
    """Instantiate a real HeadlessEnforcer pinned to a specific SID so
    the behavioural tests below can reproduce the multi-conversation
    race Francisco flagged."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from enforcement_engine import HeadlessEnforcer as _EE  # noqa: E402
    eng = _EE()
    eng._session_id = sid
    return eng


def _write_queue(queue_path, rows):
    with open(queue_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_queue(queue_path):
    if not queue_path.is_file():
        return []
    out = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            out.append({"__raw__": s})
    return out


def test_rail_multi_conv_queue_filters_by_session_id(tmp_path, monkeypatch):
    """v7.8.1 — Francisco's crash test: engine A (session_id=nexo-MINE-1)
    must NOT consume an event written for session B (nexo-OTHER-1).
    Before the fix the consumer truncated the whole queue and fired
    raise_event for every row it saw, so session A ate session B's
    event and B never recovered it.
    """
    nexo_home = tmp_path / ".nexo"
    data_dir = nexo_home / "runtime" / "data"
    data_dir.mkdir(parents=True)
    queue_path = data_dir / "pending_enforcer_events.ndjson"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    # Two events live concurrently in the global queue: one for MINE,
    # one for OTHER. Engine A is pinned to MINE.
    _write_queue(queue_path, [
        {"event": "pre_compaction",  "session_id": "nexo-MINE-1",  "claude_session_id": "cs-mine"},
        {"event": "post_compaction", "session_id": "nexo-OTHER-1", "claude_session_id": "cs-other"},
    ])

    eng = _build_engine_for_session("nexo-MINE-1")
    # Clear any queued injections so the test can read only what the
    # consumer produced.
    eng.injection_queue.clear()
    eng._consume_pending_hook_events()

    # Queue must still contain OTHER's event (it was not this session's).
    remaining = _read_queue(queue_path)
    sids = [r.get("session_id") for r in remaining]
    assert "nexo-OTHER-1" in sids, (
        "Global queue consumer MUST leave other sessions' events behind. "
        f"Got remaining={remaining}"
    )
    assert "nexo-MINE-1" not in sids, (
        "The engine's own event must be drained after consumption."
    )


def test_rail_multi_conv_queue_does_not_truncate_other_sessions_rows(tmp_path, monkeypatch):
    """Extra tight invariant: if engine A finds only its own event in
    the queue, it must consume that one and leave an empty file — but
    if there are extra rows for other sessions, they survive byte-for-
    byte across the consumer call (the file truncation trick the
    pre-v7.8.1 code did would violate this)."""
    nexo_home = tmp_path / ".nexo"
    data_dir = nexo_home / "runtime" / "data"
    data_dir.mkdir(parents=True)
    queue_path = data_dir / "pending_enforcer_events.ndjson"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    _write_queue(queue_path, [
        {"event": "pre_compaction",  "session_id": "nexo-A", "extra": 1},
        {"event": "pre_compaction",  "session_id": "nexo-B", "extra": 2},
        {"event": "pre_compaction",  "session_id": "nexo-C", "extra": 3},
    ])

    eng = _build_engine_for_session("nexo-B")
    eng.injection_queue.clear()
    eng._consume_pending_hook_events()

    remaining = _read_queue(queue_path)
    sids = sorted([r.get("session_id") for r in remaining])
    assert sids == ["nexo-A", "nexo-C"], (
        f"Only nexo-B's row must be drained; A and C must survive. Got {sids}"
    )


def test_rail_per_session_sidecar_path_is_used_when_claude_session_id_present():
    """v7.8.1 — pre-compact.sh and post-compact.sh must key the sidecar
    by CLAUDE_SESSION_ID instead of using the single global file. Two
    near-concurrent compactions on two different conversations would
    otherwise clobber each other.
    """
    pre = PRE_COMPACT_SH.read_text(encoding="utf-8")
    post = POST_COMPACT_SH.read_text(encoding="utf-8")
    # Both hooks must construct the per-conv sidecar under
    # $DATA_DIR/compacting/<token>.txt when the env token is present.
    assert 'compacting/$SAFE_CLAUDE_ID.txt' in pre, (
        "pre-compact.sh must write a per-conversation sidecar "
        "(compacting/<token>.txt) when CLAUDE_SESSION_ID is set."
    )
    assert 'compacting/$SAFE_CLAUDE_ID.txt' in post, (
        "post-compact.sh must read the per-conversation sidecar "
        "(compacting/<token>.txt) when CLAUDE_SESSION_ID is set."
    )
    # The CLAUDE_SESSION_ID fallback path (legacy global file) must
    # still exist so single-conv callers without env continue to work.
    assert 'compacting-sid.txt' in pre and 'compacting-sid.txt' in post, (
        "Both hooks must keep the legacy fallback file for callers "
        "without CLAUDE_SESSION_ID set."
    )


def test_rail_malformed_queue_lines_are_preserved_not_dropped(tmp_path, monkeypatch):
    """Bad JSON on one line must not drop another session's good row.
    v7.8.1 preserves unparseable lines so a logging glitch cannot eat
    events addressed to a different enforcer."""
    nexo_home = tmp_path / ".nexo"
    data_dir = nexo_home / "runtime" / "data"
    data_dir.mkdir(parents=True)
    queue_path = data_dir / "pending_enforcer_events.ndjson"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    queue_path.write_text(
        "not-json-at-all\n"
        + json.dumps({"event": "post_compaction", "session_id": "nexo-ME"}) + "\n"
        + "{\"partial\":\n",
        encoding="utf-8",
    )

    eng = _build_engine_for_session("nexo-ME")
    eng.injection_queue.clear()
    eng._consume_pending_hook_events()

    # My row is gone (consumed), but the two malformed lines must stay.
    raw = queue_path.read_text(encoding="utf-8")
    assert "not-json-at-all" in raw, "malformed line must survive consumer"
    assert "{\"partial\":" in raw, "partial-json line must survive consumer"
    assert "nexo-ME" not in raw, "consumed row must be drained"
