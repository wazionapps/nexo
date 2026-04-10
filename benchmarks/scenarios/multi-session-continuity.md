# Scenario: Multi-Session Continuity

## Goal

Continue a thread that spans several sessions without forcing the operator to restate the history.

## Prompt

```text
We discussed this across several sessions. Continue from the accumulated thread, not just the current prompt.
```

## Expected output

- Pulls context from more than one prior session
- Identifies the live thread or accumulated state
- Does not force the operator to restate the whole history

## Evidence anchors

- `src/plugins/workflows.py`
- `src/tools_sessions.py`
- `compare/README.md`

## Scoring

- `pass`: resumes from multi-session context cleanly
- `partial`: finds part of the thread but still needs broad restatement
- `fail`: treats the request as a fresh isolated task
