# Scenario: Prioritization Quality

## Goal

Rank pending work using explicit impact and risk signals instead of date-only ordering.

## Prompt

```text
We have several pending items. Which should go first and why?
```

## Expected output

- Ranks work by explicit impact/risk/urgency reasoning
- Uses persisted prioritization signals when available
- Avoids date-only ordering when higher-impact work exists

## Evidence anchors

- `src/plugins/impact.py`
- `src/db/_reminders.py`
- `tests/test_impact_scoring.py`

## Scoring

- `pass`: prioritization is explicitly impact-led and evidence-backed
- `partial`: better than date-only ordering but still mostly heuristic
- `fail`: effectively sorts by due date or generic intuition alone
