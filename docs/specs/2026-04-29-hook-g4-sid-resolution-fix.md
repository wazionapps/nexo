# Spec: Hook G4 SID resolution — protocol_task affinity

**Followup**: NF-HOOK-G4-SID-RESOLUTION-FIX
**Source**: 3× repro 2026-04-24 synthesis sessions; learning #596
**File**: `src/hook_guardrails.py` — `_resolve_nexo_sid` (line 551)

## Bug

When N+ aliases share a single `claude_session_id` (NEXO Desktop sub-conversations, multi-spawn synthesis),
the current resolution picks the alias with `MAX(last_seen)`. That heuristic loses to the SID that actually
owns the live `protocol_task`, so PreToolUse hook raises G4/missing_task spurious blocks. Reproduced 3× on
2026-04-24 in synthesis flow even after `ack_guard + track + cortex_decide + heartbeat` ran. Current
mitigation is a manual SQL `UPDATE session_claude_aliases SET last_seen = strftime('%s','now') WHERE sid = ?`
which requires shell access and re-races on every spawn.

## Proposed fix

Replace the alias lookup at `src/hook_guardrails.py:570-579` with a `LEFT JOIN` against `protocol_tasks`
and rank by `(has_open_task DESC, opened_at DESC, last_seen DESC)`:

```python
alias_row = conn.execute(
    """SELECT a.sid
       FROM session_claude_aliases a
       LEFT JOIN protocol_tasks t
              ON t.session_id = a.sid AND t.status = 'open'
       WHERE a.claude_session_id = ?
       ORDER BY (CASE WHEN t.task_id IS NULL THEN 0 ELSE 1 END) DESC,
                COALESCE(t.opened_at, 0) DESC,
                a.last_seen DESC
       LIMIT 1""",
    (clean_external,),
).fetchone()
```

Behaviour preserved when no SID has an open task: ranking collapses to the original
`ORDER BY last_seen DESC`, so the existing `test_resolve_env_alias_match_when_no_sessions_row` test still
passes.

## Test plan

Add a regression test in `tests/test_hook_guardrails.py`:

```python
def test_resolve_nexo_sid_prefers_sid_with_open_protocol_task():
    # Given two aliases for the same claude_session_id, where the older
    # alias owns the open protocol_task, _resolve_nexo_sid must return
    # the task-owning SID — not MAX(last_seen).
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO session_claude_aliases VALUES (?,?,?,?)",
        ("nexo-A", "claude-XYZ", 1000.0, 1000.0),
    )
    conn.execute(
        "INSERT INTO session_claude_aliases VALUES (?,?,?,?)",
        ("nexo-B", "claude-XYZ", 2000.0, 2000.0),  # newer
    )
    # nexo-A owns the open task
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, session_id, status, opened_at) "
        "VALUES (?,?,?,?)",
        ("PT-001", "nexo-A", "open", 1500.0),
    )
    conn.commit()

    from hook_guardrails import _resolve_nexo_sid
    assert _resolve_nexo_sid(conn, "claude-XYZ") == "nexo-A"
```

Plus the existing test must still pass:

```python
# test_resolve_env_alias_match_when_no_sessions_row equivalent — ensures
# MAX(last_seen) is still the tiebreaker when no alias owns an open task.
```

Run: `pytest tests/test_hook_guardrails.py tests/test_hook_runs_compact_sid_resolution.py
tests/test_hook_unknown_target_v607.py tests/test_lifecycle_events.py -x`.

## Out of scope

- `compact_session_resolver.py` is a separate path used by pre/post compact hooks; it does not need this
  change because compaction never has 2+ aliases for the same UUID at the same instant.
- Sidecar fallback (`runtime/data/compacting/`) untouched.

## Rollout

Standard: branch → tests → PR → release. No live runtime edit. Minimal blast radius — single SQL change in
one helper used only by PreToolUse hook resolution.

## Why it stayed pending 4 days

Followup created 2026-04-25; sat in PENDING with no history. Owner=shared. Headless runner produces this
spec on 2026-04-28 23:17 UTC so the fix is ready to apply in the next interactive session (or via the
weekly Evolution PR cycle if the cli.py work-in-progress on `main` clears first).
