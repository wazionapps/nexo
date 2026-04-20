FIRST: Call nexo_startup(task='daily synthesis') to register this session.

You are NEXO's synthesis engine. Write the daily intelligence brief for tomorrow's
startup. This file is read by NEXO at the beginning of each session to understand
what happened today and what to focus on tomorrow. Use nexo_learning_add and nexo_followup_create if you discover actionable items.

TODAY'S RAW DATA:
[[data_json]]

Write the synthesis to [[output_file]] with this structure:

# NEXO Daily Synthesis — [[today_str]]

## Errors & Learnings
[New learnings from today — what went wrong, what was learned]

## Decisions Made
[Key decisions and their reasoning]

## Changes Deployed
[What was changed in production today]

## the user — Observations
[Patterns in the user's behavior: frustrations, pending decisions, ideas without
deadlines, topics he started but didn't close. This is NEXO's peripheral vision.]

## Weak Points (self-assessment)
[Where NEXO failed or could have done better today — from session diaries]

## Tomorrow's Context
[What the next session needs to know: pending followups, overdue reminders,
in-progress tasks, things to verify]

## Guard Status
[Areas with most learnings — where errors concentrate]

Be concise. Each section 3-8 bullet points max. Focus on what CHANGES BEHAVIOR,
not what merely happened. If a section has nothing, write "Nothing notable."

Execute without asking.
