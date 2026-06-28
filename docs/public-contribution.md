# Public Contribution

NEXO has one public contribution path:

1. **Normal human contribution**

Automated public-core Evolution via GitHub Draft PRs is retired. Desktop-managed installs still run Evolution, but only in support-ticket mode: no branches, pushes, Draft PRs, peer reviews, transcripts, local databases, or raw private evidence leave the machine.

## Human Contributions

Use the normal GitHub flow when you are contributing manually:

- open a bug report or operational report
- include `nexo doctor --tier runtime --json` when relevant
- include parity/runtime symptoms, not only the final failure
- prefer concrete artifacts over vague summaries

## Retired Automated GitHub Contribution

Installs can no longer opt into automated public contribution through Draft PRs.

Current rules:

- never create GitHub branches, pushes, Draft PRs, or automated peer reviews from Evolution
- keep Deep Sleep, Skills, Watchdog, followups, Evolution, and normal support-ticket reporting active through their own systems
- keep Evolution enabled by default in Desktop-managed installs with `evolution_mode=support_ticket`
- redact personal scripts, local runtime data, prompts, logs, URLs, emails, client details, and secrets before any support report is sent

Legacy `draft_prs`, `public_core`, `contributor`, or `pending_auth` settings are normalized to support-ticket mode and do not create GitHub branches or PRs.

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
