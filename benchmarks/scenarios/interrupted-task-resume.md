# Scenario: Interrupted Task Resume

## Goal

Resume a task that was interrupted mid-session without redoing discovery from zero.

## Prompt

```text
Continue the release-readiness work from where we left it.
```

## Expected output

- Recovers the active workflow/task state
- States the next concrete step
- Avoids restarting the whole investigation

## Scoring

- `pass`: resumes from checkpoint with correct next action
- `partial`: finds the area but needs broad rediscovery
- `fail`: starts from scratch or guesses
