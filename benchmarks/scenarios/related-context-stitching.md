# Scenario: Related Context Stitching

## Goal

Connect a current request with related prior work that is not in the same exact thread or immediate prompt.

## Prompt

```text
Before replying, check if this issue relates to previous email monitor fixes or open followups.
```

## Expected output

- Searches for related prior work, not just the immediate request
- Surfaces relevant followup/reminder/learning context
- Treats the current item as part of a broader operational thread if evidence exists

## Scoring

- `pass`: stitches related context correctly
- `partial`: checks one related source but misses others
- `fail`: treats the item as isolated
