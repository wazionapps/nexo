# Scenario: Contradiction Latest-Wins

## Goal

Prefer the newer corrected fact over an older conflicting one.

## Prompt

```text
The operator first said dark mode, then corrected it to light mode. What is the current preference and why?
```

## Expected output

- Returns the newer preference
- Explains that the older value was superseded
- Avoids presenting both values as equally active

## Evidence anchors

- `README.md` contradiction handling and freshness claims
- `src/claim_graph.py`
- `src/scripts/deep-sleep/apply_findings.py`

## Scoring

- `pass`: latest value wins and the superseded older value is handled explicitly
- `partial`: chooses the latest value but without contradiction framing
- `fail`: repeats the stale value or treats both as equally current
