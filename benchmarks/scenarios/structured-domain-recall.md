# Scenario: Structured Domain Recall

## Goal

Retrieve only the requested domain slice from structured historical decisions.

## Prompt

```text
List the marketing decisions we made recently and exclude infrastructure work.
```

## Expected output

- Finds only the requested decision domain
- Returns more than one relevant item if evidence exists
- Avoids false positives from other domains

## Evidence anchors

- `src/plugins/episodic_memory.py`
- `src/tools_learnings.py`
- `compare/README.md`

## Scoring

- `pass`: relevant domain results only, with no obvious bleed from other work
- `partial`: mostly correct but mixes in unrelated items
- `fail`: cannot filter by domain or returns generic summaries only
