# Runtime fingerprint — when a `nexo update` requires an MCP restart

> Status: introduced in v7.11.0 (April 2026). Replaces the version-string-only restart trigger with a content-aware one.

## What problem this solves

Before v7.11.0 every `nexo update` that bumped `version.json` forced every connected MCP client (Claude Code, Codex, Claude Desktop) to restart its session, even when the release changed nothing the running server actually executes. The April 2026 cluster had several offenders — for example v7.10.1 was a README-only release, and v7.10.0 likewise touched marketing/docs after the real revert had landed in 7.9.34. Operators were paying a session-restart cost for byte-identical Python.

`runtime fingerprint` makes the decision contentful: a release only forces a restart when at least one `.py` file the running MCP imports actually changed.

## Mental model

The MCP server, when it starts, reads every Python source file under `src/` it can import. Those bytes are what defines the live behavior. Anything else in the repo — README, blog posts, launch plans, marketing markdown, gh-pages assets, JSON release manifests, even the version string itself — affects what is *published* but not what is *running*.

So we hash exactly that subset and call it the **runtime fingerprint**. Two runtimes with the same fingerprint behave identically; two with different fingerprints may not, and the safer move is to restart.

## What's in the fingerprint

The fingerprint is `sha256` over the bytes of every `.py` file under the runtime root, sorted by relative path, with the path itself fed into the hash so renames count as changes.

**Excluded subtrees** (these run out-of-process or never run at all):

- `scripts/` — invoked as subprocesses, never imported by `server.py`
- `tests/` — never executed by the live server
- `migrations/` — run once via the migration runner, not loaded by the MCP
- `crons/` — entry points spawned by launchd / cron in their own processes
- `__pycache__/`, `node_modules/`, `.git/` — build/VCS noise

**Excluded file types**: anything that isn't `.py`. Markdown, JSON, YAML, templates, presets, docs, marketing files, CHANGELOGs — none of them shift the fingerprint.

The exclusion list is intentionally short. If a future release adds a new top-level Python directory under `src/`, it's automatically covered. The bias is **"include unless we know it never executes in-process"**.

## How the gate works

`plugins/update.py` captures the **pre-update fingerprint** before pulling/installing, and recomputes the **post-update fingerprint** after the new code is in place.

```text
old_fp = compute_mcp_runtime_fingerprint(src_root)   # before pull/npm
… pull / npm update …
new_fp = compute_mcp_runtime_fingerprint(src_root)   # after

mcp_code_changed = (old_fp != new_fp)        # both present → trust the bytes
                  or not (old_fp and new_fp)  # either missing → fall back

if version_changed and (mcp_code_changed or force_restart):
    write_restart_required_marker(...)
```

A running MCP process checks the same signal on every tool call:

```text
restart_required = marker.exists
                or (installed_fp != process_fp)        # primary signal
                or (legacy: installed_version != process_version when fp unavailable)
```

`process_fp` is captured **once** at MCP startup (`prime_process_fingerprint()` in `server.py`) so it always reflects what the live process actually loaded, even after the runtime tree on disk has changed.

## Behavior matrix

| Release type | `version_changed` | `mcp_code_changed` | Marker written? | Existing clients restart? |
|---|---|---|---|---|
| Real fix in `src/plugins/foo.py` | yes | yes | yes | yes |
| New MCP tool added | yes | yes | yes | yes |
| README / blog / launch-plan only | yes | no | **no** | **no** |
| `CHANGELOG.md` only | yes | no | **no** | **no** |
| New file under `src/scripts/` | yes | no (excluded) | **no** | **no** |
| Migration in `src/migrations/` | yes | no (excluded) | **no** | **no** (migration runs out-of-process) |
| `version.json` carries `"force_restart": true` | yes | no | **yes** (`brain_update_force`) | yes |
| Fingerprint cannot be computed | yes | (treated as yes) | yes | yes |

## The escape hatch: `force_restart`

Some releases change behavior in ways the fingerprint cannot detect — e.g. the wire format of a config file the server reads via `json.load` rather than `import`. For those, set `force_restart: true` in `version.json` for that specific release and the marker is written even when the fingerprint matches. Default is `false`. This is an explicit, auditable opt-in; the releaser sets it deliberately.

## Conservative fallback (#186)

Learning #186 says: never silently leave a process running stale code after `nexo update`. The fingerprint gate honors this with two safeguards:

1. **Empty fingerprint = assume changed.** If `compute_mcp_runtime_fingerprint(...)` returns `""` (source tree missing, file unreadable, etc.), the gate treats the release as code-changing and writes the marker.
2. **`process_fp == "unknown"` disables the comparison.** If the running MCP couldn't fingerprint itself at startup (corner case during install bootstrap), `resolve_restart_required` falls back to the legacy version-string mismatch check.

The combination guarantees we never miss a real upgrade; the only thing the new logic is allowed to do is *skip* a restart that wasn't going to change anything.

## What this changes for releasers

- **Doc-only releases are now free.** Bump the version, ship the README/blog change, your operators don't lose their session. No special handling.
- **Marketing files in the repo root** (`linkedin-post.md`, `producthunt-launch.md`, etc.) never affect the fingerprint — they were never under `src/` and they're never imported.
- **Adding a new `src/plugins/foo.py`** continues to require a restart, as expected. The fingerprint changes the moment a new tracked `.py` file appears.
- **Renaming a file under `src/`** changes the fingerprint (the path is part of the hash). Restart required, which is correct because module imports changed.

## Inspection

`nexo mcp-status --json` (CLI) and `build_mcp_status()` (in-process) now expose:

- `installed_fingerprint` — the hash of what's on disk now
- `process_fingerprint` — the hash the live MCP started with
- `fingerprint_match` — boolean shortcut

Plus the existing `installed_version` / `process_version` / `version_match`.

`reason` on a forced restart is now one of:

- `marker_required` — explicit marker file present
- `marker_corrupt` — marker file unreadable, conservative force
- `fingerprint_mismatch` — primary signal: process bytes differ from disk
- `version_mismatch` — fallback signal when fingerprint unavailable

## Related learnings

- #186 — `nexo update` debe auto-reiniciar procesos/crons cuando hay bump de versión. Honored: fingerprint-aware gate plus mandatory fallback when fingerprint cannot be computed.
- #325 — TestRuntimeUpdate isolated dirs must include all top-level imports. Honored: `tests/test_runtime_fingerprint.py` builds a synthetic runtime tree per test rather than reaching into the live repo.
