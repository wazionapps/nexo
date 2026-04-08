# Scenario: User Preference Recall

## Goal

Recover a personal operating preference mentioned once weeks earlier.

## Prompt

```text
How concise should status updates be for this operator?
```

## Expected output

- States that updates should be concise
- Avoids long explanatory framing
- Connects this to stored operator preference rather than generic style advice

## Scoring

- `pass`: concise preference recalled with correct framing
- `partial`: generally concise but not grounded in stored preference
- `fail`: wrong communication style
