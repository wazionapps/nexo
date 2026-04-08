# Scenario: Decision Rationale Recall

## Goal

Recover why a decision was made seven sessions ago, including the rejected alternative.

## Setup

- Seed one prior decision with explicit alternatives.
- Reference only the later symptom in the prompt, not the answer.

## Prompt

```text
Why did we choose workflow checkpoints over plain reminders for long-running release work?
```

## Expected output

- Names workflow checkpoints as the chosen path
- Mentions replay/resume durability as the reason
- Mentions plain reminders/followups as the rejected or insufficient alternative

## Scoring

- `pass`: all three elements present
- `partial`: correct choice but missing rationale or rejected alternative
- `fail`: wrong choice or guessed answer
