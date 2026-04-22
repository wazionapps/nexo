"""v6.0.0 — Plugin mode and npm mode must register the same hook set.

Compares the set of (event, handler-basename) pairs declared by:
  - The canonical manifest ``src/hooks/manifest.json``.
  - The plugin-mode registration file ``hooks/hooks.json``.
These two sources used to drift (learning #23, #169). The test locks the
contract so a future edit that only touches one side immediately fails.

v7.3.0: manifest grew to eight entries with the PreToolUse hook that
wires Block K Guardian gates (G3 destructive + G3 SSH + G4 guard_check)
into Claude Code's tool pipeline. Without it, ``hook_guardrails.
process_pre_tool_event`` was code that never ran in production — the
post-v7.2.0 bug the operator hit on 2026-04-22.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "src" / "hooks" / "manifest.json"
PLUGIN_HOOKS = REPO_ROOT / "hooks" / "hooks.json"


def _manifest_pairs() -> set[tuple[str, str]]:
    data = json.loads(MANIFEST.read_text())
    return {(h["event"], Path(h["handler"]).name) for h in data["hooks"]}


def _plugin_hook_handlers() -> set[tuple[str, str]]:
    """Extract (event, handler basename) from the plugin hooks.json.

    The plugin definition embeds commands as shell strings; we look for the
    ``.py`` handler file referenced in each entry's ``command``.
    """
    data = json.loads(PLUGIN_HOOKS.read_text())
    pairs: set[tuple[str, str]] = set()
    for event, blocks in (data.get("hooks") or {}).items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            for hook in (block.get("hooks") or []):
                command = hook.get("command", "")
                for token in command.replace('"', ' ').split():
                    token = token.strip()
                    if token.endswith(".py"):
                        pairs.add((event, Path(token).name))
    return pairs


def test_manifest_has_nine_hooks():
    # v7.8.0: PostCompact joined the canonical set (SessionStart,
    # UserPromptSubmit, PreToolUse, PostToolUse, PreCompact,
    # PostCompact, Stop, Notification, SubagentStop). Dropping below 9
    # means a hook got de-registered — keep the floor explicit so a
    # silent removal never ships.
    pairs = _manifest_pairs()
    assert len(pairs) == 9, f"manifest should list 9 hooks, got {len(pairs)}: {pairs}"


def test_plugin_mode_matches_manifest():
    manifest = _manifest_pairs()
    plugin = _plugin_hook_handlers()

    missing_in_plugin = manifest - plugin
    extra_in_plugin = plugin - manifest
    assert not missing_in_plugin, f"plugin hooks.json missing {missing_in_plugin}"
    assert not extra_in_plugin, (
        f"plugin hooks.json has extra {extra_in_plugin} that the manifest does not own"
    )


def test_manifest_includes_new_v6_hooks():
    events = {event for (event, _) in _manifest_pairs()}
    assert "Notification" in events
    assert "SubagentStop" in events


def test_manifest_includes_v7_3_pre_tool_use_hook():
    """v7.3.0 wired PreToolUse into the manifest to close the Block K Guardian
    gap. Without this hook, ``hook_guardrails.process_pre_tool_event`` is
    unreachable from Claude Code and Guardian hard is silently no-op.
    """
    pairs = _manifest_pairs()
    assert ("PreToolUse", "pre_tool_use.py") in pairs, (
        f"manifest must register the NEXO core PreToolUse hook: {pairs}"
    )
