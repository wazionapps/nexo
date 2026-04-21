# Continuity Notes Protocol

Status: draft v0.1 — 2026-04-21
Owner: NEXO Brain (continuity/compaction reliability)
Source followup: `NF-DS-67F8DCF5` (Deep Sleep, recurring pattern: "pérdida de continuidad tras compactación por no escribir notas continuas en archivo .txt/.md")

## Why this protocol exists

Claude Code (and any long-running assistant that can compact or truncate its context) periodically loses in-session continuity when:

- A phase closes and only the compressed summary survives.
- A new directive arrives mid-session and only "current" context remembers it.
- A blocker lands late and gets lost after the next compaction.
- A checklist `[ ] / [x]` is updated mentally but never written down.

The Deep Sleep synthesis on 2026-04-20 flagged this as a recurring failure: in a marathon session, Francisco had to repeat "te vas dejando un txt con notas joder" three times. After each compaction, the agent lost track of the master plan (`nexo-auditoria-alineacion-general.txt`) and had to be re-oriented.

Conclusion: in-context memory **does not** survive compaction in a way we can rely on. The agent needs an on-disk continuity file it updates continuously, so the post-compact state can read it and continue cleanly.

## Scope

Applies to any NEXO-operated Claude Code / Codex / Desktop session that is:

- Long-running (>1 hour of active work), OR
- Plan-driven (a written plan / audit / directive is being executed), OR
- Multi-phase (has explicit `[ ] / [x]` checkpoints the operator cares about).

Single-message tasks and pure triage cycles are exempt.

## Rules (v0.1)

### R1 — Create a continuity file at the first milestone

A milestone is whichever lands first:

- Agent completes the first non-trivial task (`nexo_task_close` with `outcome=success|partial`).
- Agent opens a workflow (`nexo_workflow_open`) or writes a multi-step plan.
- Operator drops a directive the agent plans to execute over several steps.

When a milestone lands, the agent writes (or appends to) the session continuity file:

```
$NEXO_HOME/runtime/continuity/<session_id>.md
```

Minimum skeleton:

```markdown
# Continuity — <session_id>
Started: <UTC ISO>
Master plan: <path or "none">
Directive: <one-line from operator, or "none">

## Open phases
- [ ] <phase title> — <status>

## Open blockers
- <blocker> — <owner>

## Decisions already made
- <decision> — <UTC>

## Next action
- <one concrete next step>
```

### R2 — Update on every meaningful event

The file is rewritten (or appended) on **any** of:

- Phase close (`[ ] -> [x]`).
- New directive from the operator.
- New blocker.
- Decision that replaces a prior plan.
- Any `nexo_task_close` with `outcome=success` on a non-trivial task.
- Any `nexo_workflow_update` with a material state change.

One-line rule: **if a future-you reading only this file should know about it, write it down.**

### R3 — Never assume in-context memory survives compaction

The agent must treat the continuity file as the single authoritative record of live state. After any of:

- `PreCompact` hook firing.
- Session resume after a gap.
- `nexo_checkpoint_read` returning a checkpoint from before the current phase.

…the agent **reads the continuity file first** and restores its plan from it, not from summary lines embedded in the conversation.

## Hook integration contract (v0.1 design — NOT YET IMPLEMENTED)

This section is the contract the PreCompact hook (`src/hooks/pre-compact.sh`) will satisfy in a later patch. It is documented here so the patch can be reviewed against an agreed spec.

1. On fire, the hook resolves the current active session ID (already does this to write the emergency diary).
2. Compute `continuity_path = $NEXO_HOME/runtime/continuity/<sid>.md`.
3. If the file exists, inject a `systemMessage` with its path so the post-compact state re-reads it immediately:
   - Preferred path: the post-compact hook echoes a `Core Memory Block` with `continuity_path` and the file's mtime.
4. If the file does NOT exist AND the session is eligible (see Scope above — the hook will treat any session with >20 tool calls since last diary as eligible), create a stub file using the skeleton above and warn the agent via `systemMessage` so it knows to start writing.
5. The hook never overwrites an existing continuity file. It only creates stubs and surfaces the path.

### Eligibility heuristic

The hook uses a conservative heuristic to avoid creating noise for short sessions:

- Eligible: tool log entries since the last diary >= 20, OR `session_checkpoints.task` contains one of `audit|plan|workflow|directive|consolidac|triage`.
- Not eligible: any session where the task matches `triage-only|email-only|quick-check|followup-runner-cycle`.

### Failure mode

The hook must NOT block compaction if it cannot create the file (disk full, permission error, etc.). It logs the failure and proceeds.

## Open questions for operator review

1. Does the continuity file live under `runtime/continuity/` (per-session) or under `operations/continuity/` (per-domain, overwritten)? Current proposal: `runtime/continuity/<sid>.md` because each session is the natural unit.
2. Should the continuity file be readable by other sessions (shared brain parity), or private to its own session? Current proposal: readable by any session, but writable only by the owning session.
3. Should `systemMessage` from the PreCompact hook be hard (blocking) or soft (advisory)? Current proposal: soft — the agent must read the file, but compaction is never blocked.
4. Do we want a dashboard view of active continuity files? Current proposal: yes, but ship in a later iteration.

## Validation before wider rollout

Before the hook patch lands in the release flow:

- Unit test: hook creates stub, doesn't overwrite existing, handles missing session ID gracefully.
- Integration test: on a simulated PreCompact event, continuity file ends up in the expected path with the expected headers.
- Operator test: one live session run end-to-end with the new hook active, continuity file inspected manually before next release.

## Change log

- v0.1 (2026-04-21) — initial draft, rules R1/R2/R3 + hook integration contract. Hook code NOT yet modified.
