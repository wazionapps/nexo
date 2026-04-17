"""v6.0.0 — Plugin mode and npm mode must register the same seven hooks.

Compares the set of (event, handler-basename) pairs declared by:
  - The canonical manifest ``src/hooks/manifest.json``.
  - The plugin-mode registration file ``hooks/hooks.json``.
These two sources used to drift (learning #23, #169). The test locks the
contract so a future edit that only touches one side immediately fails.
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


def test_manifest_has_seven_hooks():
    pairs = _manifest_pairs()
    assert len(pairs) == 7, f"manifest should list 7 hooks, got {len(pairs)}: {pairs}"


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
