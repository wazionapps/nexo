# Guardian Hooks Classification

This document classifies the hook surfaces that currently exist in
`src/hooks/` so Brain, Desktop, audits, and release work all talk about the
same runtime model.

## Canonical Model

The canonical hook contract is the Python manifest in
`src/hooks/manifest.json`.

That means the official runtime-facing hooks are:

| Event | Canonical handler | Status |
| --- | --- | --- |
| `SessionStart` | `session_start.py` | active |
| `UserPromptSubmit` | `auto_capture.py` | active |
| `PostToolUse` | `post_tool_use.py` | active |
| `PreCompact` | `pre_compact.py` | active |
| `Stop` | `stop.py` | active |
| `Notification` | `notification.py` | active |
| `SubagentStop` | `subagent_stop.py` | active |

`client_sync.py` already treats these Python handlers as the source of truth and
uses legacy hook identities only to prune stale installs from older runtimes.

## Active

These are directly installed and invoked as part of the current managed hook
pipeline.

- `session_start.py`
- `auto_capture.py`
- `post_tool_use.py`
- `pre_compact.py`
- `stop.py`
- `notification.py`
- `subagent_stop.py`

## Legacy Internal

These are not installed as first-class hooks anymore, but they still serve as
internal subroutines behind the unified Python handlers. They remain product
code, but the runtime should think of them as implementation details, not as
independent hook surfaces.

- `daily-briefing-check.sh`
- `session-start.sh`
- `capture-tool-logs.sh`
- `capture-session.sh`
- `inbox-hook.sh`
- `protocol-guardrail.sh`
- `heartbeat-posttool.sh`
- `pre-compact.sh`
- `session-stop.sh`
- `heartbeat-enforcement.py`

Current delegation:

- `session_start.py` wraps `daily-briefing-check.sh` and `session-start.sh`
- `post_tool_use.py` wraps `capture-tool-logs.sh`, `capture-session.sh`,
  `inbox-hook.sh`, `protocol-guardrail.sh`, `heartbeat-posttool.sh`, and then
  runs `auto_capture.py`
- `pre_compact.py` wraps `pre-compact.sh`
- `stop.py` wraps `session-stop.sh`
- `heartbeat-enforcement.py` remains an internal helper for
  `heartbeat-posttool.sh`

## Legacy To Remove

These belong to the old direct-shell registration model. They should not be
installed as managed hooks anymore, and current sync/update code already treats
them as stale identities to prune or clean up.

- `protocol-pretool-guardrail.sh`
- `heartbeat-user-msg.sh`
- `heartbeat-guard.sh` (legacy identity still pruned from old clients)

Signals that these are retired rather than active:

- `client_sync.py` tracks them under `LEGACY_CORE_HOOK_IDENTITIES_BY_EVENT`
  only to remove stale managed hooks
- `tests/test_client_sync.py` asserts they are rewritten away
- `auto_update.py::_cleanup_retired_runtime_files()` removes the old runtime
  copies for `heartbeat-user-msg.sh` and `heartbeat-guard.sh`

## Experimental Or Dormant

These exist on disk but are not part of the managed manifest-driven runtime
contract today.

- `post-compact.sh`
- `caffeinate-guard.sh`

Interpretation:

- `post-compact.sh` is a dormant continuity surface from the pre-manifest era.
  It is still useful as a reference implementation, but it is not currently
  installed by `client_sync.py` because the managed manifest has no
  `PostCompact` event.
- `caffeinate-guard.sh` is an out-of-band machine helper kept in the hooks
  folder, but it is not part of the current Claude hook pipeline.

If either one becomes part of the supported runtime again, it should first be
promoted into the manifest-driven model instead of being reintroduced as an
ad-hoc shell-only hook.

## Operational Rule

When auditing or extending hooks:

1. Add new runtime-facing behavior to the manifest-driven Python layer first.
2. Keep shell scripts only when they are clearly bounded internal helpers.
3. Treat old direct-shell identities as migration debris unless the manifest
   explicitly restores them.
