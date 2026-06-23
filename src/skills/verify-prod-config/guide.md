# Verify Production Configuration

Use this skill before answering any question about production configuration when the candidate source is an internal document, launch note, deployment note, migration plan, transcript, or legacy memory.

Production configuration includes Stripe, SMTP/mail, domains/DNS, provider accounts, OAuth apps, tax/IGIC/VAT, billing plans, credentials, and runtime feature flags.

## Required Checklist

1. Identify the exact production surface:
   - project / tenant:
   - host / service:
   - config keys or claims being answered:
   - internal docs being compared:

2. Read the live runtime configuration:
   - Use the registered server/source from project-atlas first.
   - For server apps, SSH to the production host and read the real runtime `.env` or equivalent config file.
   - Never paste secrets into the answer, notes, or logs. Record only key names, boolean presence, account mode, hostnames, redacted IDs, and timestamps.

3. Verify through the real provider/API:
   - Stripe: call the live Stripe API enough to confirm account/mode/price/tax claim.
   - SMTP/mail: call the real mail provider, send-log DB, or authenticated config endpoint.
   - Domains/DNS: run live DNS/API checks for the relevant zone and record.
   - Providers/OAuth/tax: call the provider API or admin endpoint that proves the claim.

4. Compare live evidence against internal docs:
   - Treat `DEPLOY-NOTES.md`, `LAUNCH-NOTES.md`, `MIGRATION-PLAN-GCP.md`, and legacy memory notes as stale unless they were refreshed in the last 14 days and match live evidence.
   - If live evidence differs from the document, mark the document as `STALE` in the work notes and open a followup to update or retire it.
   - Do not answer from the document until the live mismatch is resolved or clearly disclosed.

5. Answer only from the verified state:
   - State the verified production source and timestamp.
   - State the internal doc status: matched, stale, or not used.
   - Keep redaction strict: no secrets, tokens, passwords, or full credential values.

## Closure Evidence

Include this checklist in the task close or followup note:

- [ ] project-atlas or registered source checked
- [ ] production `.env` or equivalent runtime config read
- [ ] real provider/API checked
- [ ] internal docs compared
- [ ] stale docs marked and followup opened if mismatch found
- [ ] answer uses live evidence, not doc-only memory
- [ ] secrets redacted
