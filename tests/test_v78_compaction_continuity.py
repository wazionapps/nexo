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


def test_engine_consumer_truncates_queue_after_read():
    """Rail 4 part 3: a row must not fire twice. The consumer must
    truncate the queue file after reading."""
    src = ENGINE.read_text(encoding="utf-8")
    idx = src.find("def _consume_pending_hook_events(self)")
    assert idx != -1
    window = src[idx:idx + 3000]
    assert 'open(queue_path, "w"' in window or 'open(queue_path,"w"' in window, (
        "consumer must truncate the queue file after reading so events "
        "never fire twice."
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
