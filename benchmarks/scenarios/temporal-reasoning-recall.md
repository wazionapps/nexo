# Scenario: Temporal Reasoning Recall

## Goal

Answer a historical query for an earlier point in time instead of collapsing everything into the final state.

## Prompt

```text
What did we know about this issue last Tuesday, before the later fix landed?
```

## Expected output

- Answers for the earlier point in time, not only the final state
- Uses temporal framing explicitly
- Avoids collapsing the timeline into one undated summary

## Evidence anchors

- `README.md` temporal indexing section
- `src/cognitive/_search.py`
- `tests/test_cognitive.py`

## Scoring

- `pass`: historical state recovered with correct temporal framing
- `partial`: generally relevant but mixes earlier and later state
- `fail`: answers only with the final current state or guesses
