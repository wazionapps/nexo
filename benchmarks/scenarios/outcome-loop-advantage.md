# Scenario: Outcome-Loop Advantage

## Goal

Choose a strategy using persisted measured outcomes instead of pure intuition.

## Prompt

```text
Choose the strategy that has worked better historically and explain the evidence.
```

## Expected output

- Uses persisted outcome evidence, not only intuition
- Explains which prior strategy performed better
- Shows the loop from action to measured result to adjustment

## Evidence anchors

- `src/plugins/outcomes.py`
- `src/plugins/cortex.py`
- `release-contracts/v4.5.0.json`

## Scoring

- `pass`: uses explicit historical outcome evidence and explains the adjustment
- `partial`: acknowledges outcomes but still relies mostly on generic reasoning
- `fail`: no outcome evidence in the reasoning path
