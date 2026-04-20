# Product Operator Wiki

This is the compact internal wiki for agents operating on NEXO as a product.

Use it before modifying Brain, Desktop, automations, onboarding, release surfaces, or personal artifact flows. The goal is to keep one stable explanation of what each layer is and where extensions belong.

## 1. What NEXO Brain is

NEXO Brain is the open-source runtime and source of truth.

- It owns the canonical behavior for runtime paths, automations, prompts, migrations, health checks, portability, rules, and parity contracts.
- It must remain installable and usable without NEXO Desktop.
- Product behavior that should ship to every user belongs in the Brain repo, not in personal runtime folders.

## 2. What NEXO Desktop is

NEXO Desktop is the managed product surface on top of NEXO Brain.

- It packages the Brain runtime for non-technical operators.
- It is responsible for onboarding, managed updates, Claude login flow, product-facing preferences, legal gates, and approachable UI.
- It should consume Brain as the source of truth instead of re-implementing runtime contracts in parallel.

## 3. Runtime layers you must not mix

Keep these layers separate before making changes:

1. Product source repos:
   - Brain repo
   - Desktop repo
2. Installed runtime:
   - `~/.nexo/core`
   - `~/.nexo/runtime`
3. Operator data:
   - `~/.nexo/personal/*`

Rules:

- Fix product behavior in source first.
- Move product code into live installs through release/update, not by editing installed `core/` directly.
- Preserve personal data when cleaning or migrating installs.
- Remove superseded personal artifacts only after the core replacement is real, configured, and active.

## 4. Extension map

Use the right surface for the right job:

- Product-wide behavior -> Brain repo change
- Managed product UX -> Desktop repo change
- Local automation -> personal script or core automation, depending on whether all users need it
- Reusable agent procedure -> skill
- New callable runtime tool -> plugin

Canonical docs for these decisions:

- `docs/personal-artifacts-manual.md`
- `docs/runtime-templates.md`
- `docs/writing-scripts.md`
- `docs/skills-v2.md`

## 5. Automation contract

The current core product automations are:

- `email-monitor`
- `followup-runner`
- `morning-agent`
- `nexo-send-reply.py` as required helper dependency

Product rules:

- Brain defines the runtime truth.
- Desktop exposes them as managed product controls.
- Each automation can be enabled/disabled, scheduled, and given operator extra instructions without editing the master prompt.
- Health depends on the required email/account configuration being present.

## 6. Identity contract

- The agent presents as the configured assistant name.
- If architecture context matters, the agent says it runs on NEXO Brain architecture.
- The agent must not present itself primarily as a generic LLM/model/vendor persona.
- `NEXO`, `NEXO Brain`, and `NEXO Desktop` are product names, not assistant defaults.

## 7. Release contract

When working on product releases:

- Brain and Desktop must stay aligned.
- Public surfaces must match the actual product state.
- QA artifacts must stay separate from distributable/public artifacts.
- Do not treat a local unsigned QA DMG as a public release artifact.

Canonical release surfaces:

- `README.md`
- `CHANGELOG.md`
- `llms.txt`
- `index.html`
- `blog/`
- `changelog/`
- `sitemap.xml`

## 8. Minimum decision order before acting

Use this order every time:

1. Check code/tests for the real runtime contract.
2. Check this wiki.
3. Check `docs/agent-product-playbook.md`.
4. Check `docs/personal-artifacts-manual.md` and `docs/runtime-templates.md`.
5. Check the relevant release/public surfaces if the change is user-visible.

If the answer is still unclear after that, add/update docs before adding more product surface.
