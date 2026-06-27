# Public Contribution

NEXO has one public contribution path:

1. **Normal human contribution**

Automated public-core Evolution via GitHub Draft PRs is retired. Desktop-managed installs no longer run Evolution cycles, create branches, push PRs, open peer reviews, or create Evolution support tickets.

## Human Contributions

Use the normal GitHub flow when you are contributing manually:

- open a bug report or operational report
- include `nexo doctor --tier runtime --json` when relevant
- include parity/runtime symptoms, not only the final failure
- prefer concrete artifacts over vague summaries

## Retired Automated Public-Core Evolution

Installs can no longer opt into automated public contribution through Draft PRs.

Current rules:

- never create GitHub branches, pushes, Draft PRs, or automated peer reviews from Evolution
- keep Deep Sleep, Skills, Watchdog, followups, and normal support-ticket reporting active through their own systems
- keep Evolution disabled/retired by default in Desktop-managed installs
- redact personal scripts, local runtime data, prompts, logs, URLs, emails, client details, and secrets before any non-Evolution support report is sent

Legacy `draft_prs`, `support_ticket`, or `pending_auth` settings are treated as retired inputs and do not create tickets, branches, or proposals.

## Best Reports For NEXO

The highest-value community input is not generic “it broke”.

The best reports include:

- exact command or tool path used
- client/backend involved: Claude Code, Codex, Claude Desktop
- expected vs actual behavior
- runtime doctor output
- whether the issue is startup, parity, Deep Sleep, automation, hooks, or support-ticket routing

## Files That Help New Contributors

- `.github/ISSUE_TEMPLATE/operational_report.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/PULL_REQUEST_TEMPLATE.md`

These templates are tuned to feed NEXO with concrete operational evidence instead of vague bug descriptions.
