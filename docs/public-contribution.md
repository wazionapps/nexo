# Public Contribution

NEXO has one public contribution path:

1. **Normal human contribution**

Automated public-core Evolution via GitHub Draft PRs is retired. Evolution remains active, but Desktop-managed installs route improvement requests to anonymized NEXO support tickets instead of creating branches, pushes, PRs, or peer reviews.

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
- keep Evolution enabled by default for product improvement detection
- route improvement requests to support-ticket mode
- redact personal scripts, local runtime data, prompts, logs, URLs, emails, client details, and secrets before any support ticket is sent

Legacy `draft_prs` or `pending_auth` settings are treated as retired inputs and migrated to support-ticket mode.

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
