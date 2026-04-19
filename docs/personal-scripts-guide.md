# Personal Scripts Guide

> How to invoke the NEXO automation backend from a user-owned script without touching the NEXO Brain repo.

## When to use this

You are a NEXO session helping Francisco (or any operator) author a new script that lives in `~/.nexo/scripts/`. The script needs to call the LLM via `run_automation_prompt` / `run_automation_json`. Follow this guide so the script respects the v6+ protocol.

If you are adding a caller that lives inside the `src/` tree of `wazionapps/nexo` itself, this guide does **not** apply — register the caller in `src/resonance_map.py::SYSTEM_OWNED_CALLERS` (or `USER_FACING_CALLERS` for interactive entry points) with a deliberate tier.

## TL;DR

```python
from nexo_helper import run_automation_json

payload = run_automation_json(
    "Your prompt here",
    caller="personal/morning-briefing",   # REQUIRED, must start with "personal/"
    tier="alto",                          # optional — "maximo" | "alto" | "medio" | "bajo"
    # Alternatively, specify effort directly:
    # reasoning_effort="xhigh",
)
```

The same kwargs exist on `run_automation_text(...)`. At the CLI level, `nexo-agent-run.py` exposes `--caller` and `--tier`.

## The rules

1. **`caller` is mandatory** and must start with `"personal/"`. Anything outside that prefix still has to be registered in `src/resonance_map.py`; the personal prefix is the only escape hatch.
2. **Pick a stable, descriptive id** after the prefix. Good: `personal/morning-briefing`, `personal/email-monitor`, `personal/github-watch`. Bad: `personal/test`, `personal/x`, `personal/tmp`.
3. **Pick a tier that reflects reasoning demand**, not cost:
    - `maximo` — critical decisions, multi-source synthesis, planning that must not hedge.
    - `alto` (default) — most productive agent work, user-facing output where quality is visible.
    - `medio` — mechanical extraction, classification, summarisation against a fixed schema.
    - `bajo` — cheap polls, health checks, simple templated summaries.
4. **If the script must always run at a specific tier regardless of user preference**, pass `tier="..."` explicitly.
5. **If the script should follow the user's global default**, pass neither `tier` nor `reasoning_effort`. NEXO resolves from `calibration.preferences.default_resonance` (or `DEFAULT_RESONANCE="alto"` as a last resort).
6. **Never hardcode a model string.** The tier → model mapping lives in `src/resonance_tiers.json` and can change silently between Brain versions (e.g. the Opus 4.6 → 4.7 bump in v5.6.0 migrated every tier-driven script without requiring edits).

## Precedence

When `resolve_tier_for_caller` sees a `personal/*` caller, it evaluates in this order and returns on the first valid hit:

1. `explicit_tier` passed by the script (`tier="..."` kwarg).
2. `user_default` passed explicitly (rare; mostly internal).
3. `preferences.default_resonance` from `brain/calibration.json` on disk.
4. `DEFAULT_RESONANCE` (`"alto"`).

An invalid tier value (typo, unsupported string) is treated as "no hint" and the resolver falls through to the next step. There is no silent crash.

## Anti-patterns

| Don't | Do |
|---|---|
| `model="claude-opus-4-7[1m]"` | `tier="alto"` |
| `caller="my-script"` (no prefix) | `caller="personal/my-script"` |
| `caller="agent_run/generic"` (reserved fallback) | `caller="personal/<descriptive-id>"` |
| `reasoning_effort="xhigh"` always | `tier="alto"` (semantic) |
| Adding a new entry to `SYSTEM_OWNED_CALLERS` for a personal script | Use the `personal/` prefix and pin `tier=` at call site |

## Minimal example

```python
#!/usr/bin/env python3
"""Morning briefing — summarises last night's activity for Francisco."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".nexo" / "templates"))
from nexo_helper import run_automation_json

payload = run_automation_json(
    "Summarise the last 12 hours of activity across my projects. Return "
    "JSON with keys `highlights`, `blockers`, `decisions_pending`.",
    caller="personal/morning-briefing",
    tier="alto",
)

print(payload)
```

## Testing your personal script

Never run against the host's real `~/.nexo`. Export a scratch `NEXO_HOME` first:

```bash
NEXO_HOME=/tmp/nexo-test-$(date +%s) python3 my_script.py
```

The runtime creates the temp home on the fly, runs migrations, and leaves it behind for inspection.

## If this guide is out of date

The source of truth is `src/resonance_map.py::resolve_tier_for_caller`. When that function disagrees with this file, the function wins — fix the docs. The relevant invariants the function enforces at runtime:

- A `personal/*` caller **never** raises `UnregisteredCallerError`.
- A non-`personal/*` caller that is not in either registry **always** raises `UnregisteredCallerError`.
- `resolve_model_and_effort` looks up `(tier, backend)` in `src/resonance_tiers.json` and returns `("", "")` for an unknown backend so the caller can provide explicit arguments instead.

## See also

- `src/resonance_map.py` — the resolver and the two registries.
- `src/resonance_tiers.json` — the canonical `tier → (model, effort)` per backend mapping.
- `CHANGELOG.md` — the v6.0.2 entry introducing the `personal/` prefix.
