# Workflow Quickstart

NEXO workflows are for multi-step work that should survive session restarts, client switches, retries, and approval gates.

Use them when a task is too large to trust to transient chat state.

## Mental model

- `nexo_workflow_open` creates the durable run
- `nexo_workflow_update` records step progress, checkpoints, retries, and next action
- `nexo_workflow_resume` tells you the next honest action
- `nexo_workflow_replay` shows the recent execution history
- `nexo_workflow_handoff` transfers ownership cleanly to another client or session

## Example 1: Release work across multiple sessions

Open the workflow:

```bash
nexo call nexo_workflow_open --input '{
  "sid":"YOUR_SESSION_ID",
  "goal":"Prepare and publish v3.1.0",
  "owner":"codex",
  "priority":"high",
  "workflow_kind":"release",
  "steps":"[
    {\"key\":\"doctor\",\"title\":\"Run release readiness checks\"},
    {\"key\":\"package\",\"title\":\"Build and verify artifacts\"},
    {\"key\":\"publish\",\"title\":\"Publish release\"}
  ]",
  "next_action":"Run doctor and capture the first checkpoint."
}'
```

Record the first step:

```bash
nexo call nexo_workflow_update --input '{
  "run_id":"WF-...",
  "step_key":"doctor",
  "step_title":"Run release readiness checks",
  "step_status":"completed",
  "summary":"doctor clean, parity checks passed",
  "evidence":"nexo doctor + verify_client_parity.py",
  "next_action":"Build release artifacts."
}'
```

If the session dies, do not reconstruct from memory. Resume honestly:

```bash
nexo call nexo_workflow_resume --input '{"run_id":"WF-..."}'
nexo call nexo_workflow_replay --input '{"run_id":"WF-...","limit":5}'
```

## Example 2: Human approval gate

Mark a step as waiting for approval:

```bash
nexo call nexo_workflow_update --input '{
  "run_id":"WF-...",
  "step_key":"publish",
  "step_title":"Publish release",
  "step_status":"blocked",
  "requires_approval":true,
  "summary":"Artifacts are ready. Waiting for explicit publish approval.",
  "next_action":"Wait for approval before publishing."
}'
```

When approval arrives, continue the same run instead of opening a new one:

```bash
nexo call nexo_workflow_update --input '{
  "run_id":"WF-...",
  "step_key":"publish",
  "step_title":"Publish release",
  "step_status":"in_progress",
  "summary":"Approval received. Publishing now.",
  "next_action":"Run publish commands and capture final evidence."
}'
```

## Example 3: Cross-client handoff

If Codex starts the work and Claude Code should finish it:

```bash
nexo call nexo_workflow_handoff --input '{
  "run_id":"WF-...",
  "actor":"codex",
  "new_owner":"claude_code",
  "handoff_note":"Doctor and package steps are done. Publish step is waiting on final smoke test.",
  "next_action":"Run smoke test and publish if clean."
}'
```

On the receiving client:

```bash
nexo call nexo_workflow_resume --input '{"run_id":"WF-..."}'
nexo call nexo_workflow_get --input '{"run_id":"WF-...","include_steps":true}'
```

## Replay vs resume

- Use `resume` when you want the next actionable step
- Use `replay` when you need the recent execution trail
- Use `get` when you need the full stored state

## Good habits

- Open a workflow for long-running or cross-session tasks, not for tiny one-shot edits
- Keep `next_action` concrete
- Record evidence in `workflow_update` instead of leaving state implicit
- Use handoff instead of writing prose summaries in chat when another client should continue
