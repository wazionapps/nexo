# Migrating Nora (Maria's Mac) to F0.6

> Audience: Francisco. Requires a **synchronous window with Maria present**
> (>=2 h) because macOS login prompts + Tailscale SSH may require Maria to
> approve them interactively at her end.

## What this covers

Two scripts, used in sequence, take a pre-F0.6 NEXO install on Maria's
Mac up to the current physical F0.6 layout, rename the agent identity
to `Nero`, install the local classifier (lazy ~570 MB), rewrite
LaunchAgent paths, and leave a rollback snapshot.

- `scripts/f0-safe-apply-remote.sh maria` — F0.0 → F0.5-safe.
  Symlink-only migration. Keeps the legacy flat layout visible while
  the new one becomes canonical. Safe to run any time.
- `scripts/nexo-migrate-nora.sh maria [--apply]` — F0.0/F0.5 → F0.6
  physical layout. Snapshots, cleans junk, renames agent, migrates,
  installs classifier, reloads LaunchAgents. **Dry-run by default.**

The two scripts can run independently. `nexo-migrate-nora.sh` calls
`f0-safe-apply-remote.sh` internally in Phase 2 if the remote is still
pre-F0.5.

## Preflight (do once before the window)

1. Confirm SSH alias works: `ssh maria 'uname -a'` returns darwin.
2. Confirm NEXO CLI installed on remote: `ssh maria 'nexo --version'`.
3. Confirm Maria agrees on the window: mention that her Mac may briefly
   lose NEXO automations (30–60 seconds) during Phase 7 LaunchAgent
   reload.
4. Pull latest Brain repo locally so both scripts are current.
5. Stage Maria's ad-hoc personal work: `ssh maria 'cd ~/.nexo/personal/scripts && ls "*"*2*'`.
   The migration deletes `" 2"` / `.bak` duplicates — confirm with Maria
   none of them are work-in-progress she wants to keep.

## Dry-run first (no mutation)

```
cd /Users/franciscoc/Documents/_PhpstormProjects/nexo
scripts/nexo-migrate-nora.sh maria
```

Reads the remote state, prints every phase's plan, touches nothing.
Output ends with `Phase 8: doctor --all (plan)` and `Done.`

Review the output with Maria. Confirm you see:
- `Remote structure-version: none` or `F0.0` or `F0.5*` (any of these are
  valid entry points).
- Phase 3 listing the junk files that will be removed in `--apply` mode.
- No surprise entries — if a file under `~/.nexo/personal/scripts/`
  looks like active work, stop and coordinate with Maria.

## Apply

```
scripts/nexo-migrate-nora.sh maria --apply
```

Phase timing (approximate):
| Phase | Action                          | Wall-clock |
|-------|----------------------------------|-----------|
| 0     | preflight                        | <5 s      |
| 1     | snapshot (~/.nexo copy)          | 5–40 s    |
| 2     | F0.5-safe (only if needed)       | 10–30 s   |
| 3     | clean junk                       | <5 s      |
| 4     | calibration rename               | <5 s      |
| 5     | nexo update (F0.6 physical)      | 30–180 s  |
| 6     | classifier install (lazy)        | seconds to kick off; hours in background |
| 7     | LaunchAgent reload               | 10–30 s   |
| 8     | doctor --all                     | 5–15 s    |

The whole foreground part should complete in under 10 minutes. The
classifier keeps downloading in the background for up to several hours
on slow connections; Maria does not need to wait for it.

## Post-apply verification (with Maria at her Mac)

1. `ssh maria 'nexo doctor --tier boot --json'` → no `critical`.
2. `ssh maria 'launchctl list | grep com.nexo.'` → all expected agents
   visible, no `PID=-` on the ones that should be keep-alive.
3. Ask Maria to open NEXO Desktop (if she has it) and confirm the
   welcome greets her with `Nero` (not `NEXO`).
4. `ssh maria 'tail -20 ~/.nexo/runtime/logs/watchdog.log'` → recent
   timestamps, no tracebacks.
5. Leave the `~/.nexo-pre-f06-snapshot-<stamp>` for at least 7 days.
   Only after Maria confirms everything works for a week do we remove
   it: `ssh maria 'rm -rf ~/.nexo-pre-f06-snapshot-*'`.

## Rollback

If Phase 5+ fails or behavior is clearly broken:

```
ssh maria 'nexo rollback f06 --yes --keep-agents-running'
```

The CLI (shipped in v7.1.11+) does the safe two-stage swap:
1. renames current `~/.nexo` to `~/.nexo-rollback-backup-<stamp>`
2. restores `~/.nexo-pre-f06-snapshot-<stamp>` into `~/.nexo`

Then manually:

```
ssh maria 'for p in $HOME/Library/LaunchAgents/com.nexo.*.plist; do
  launchctl unload "$p" 2>/dev/null; launchctl load "$p"; done'
```

## Items NOT covered by these scripts

These require decisions Francisco makes with Maria present, they are
not automated:

- Opt-in for the Desktop product surface (Maria chooses terminal-only
  vs NEXO Desktop). Affects `com.nexo.dashboard` expectations — see
  `docs/f06-layout-contract.md` §4.
- Classifier-heavy workflow activation (auto-capture, embeddings in
  find_similar_*). These kick in automatically once the model is
  installed, but Maria may prefer a phased roll-out. Monitor with
  `ssh maria 'ls -la ~/.cache/huggingface/'`.
- `~/.claude/settings.json` legacy-path rewrite: Claude Code settings
  normally do not embed NEXO paths, but verify during window. If Maria
  ever hand-edited absolute paths (`~/.nexo/scripts/foo` etc.), patch
  them to the F0.6 equivalent (`~/.nexo/core/scripts/foo` or leave them
  if the compatibility symlink still exists).

## Change log

- 2026-04-22: initial version, F0.5-safe helper + nora-migrate. Covers
  master checklist items Brain B3 (nora migrate + f0.safe-apply-remote
  + calibration rename + classifier + 44-junk cleanup + LaunchAgent
  rewrite + settings.json note).
