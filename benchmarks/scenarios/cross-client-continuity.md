# Scenario: Cross-Client Continuity

## Goal

Carry shared-brain context from one client to another without manual copy-paste.

## Prompt

```text
Continue in Codex what was started in Claude Code without losing the brain state.
```

## Expected output

- Recovers work initiated in another client
- Relies on shared brain/runtime artifacts, not manual pasteback
- Keeps parity constraints explicit

## Evidence anchors

- `scripts/verify_client_parity.py`
- `docs/client-parity-checklist.md`
- `README.md` shared brain section

## Scoring

- `pass`: cross-client continuity works and the answer reflects shared state
- `partial`: preserves high-level context but loses workflow/task fidelity
- `fail`: cannot bridge clients without manual restatement
