from pathlib import Path
import json
import re


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "nexo-brain.js"
TEXT = SOURCE.read_text(encoding="utf-8")
PACKAGE = json.loads((Path(__file__).resolve().parents[1] / "package.json").read_text(encoding="utf-8"))


def test_desktop_managed_defers_claude_install_until_final_sync():
    assert 'if (desktopManaged && (client === "claude_code" || client === "codex")) {' in TEXT
    assert '`${runtimeClientLabel(client)} install deferred to Desktop final sync.`' in TEXT


def test_desktop_managed_keeps_claude_automation_enabled_for_final_sync():
    pattern = re.compile(
        r'if \(setup\.automation_enabled && setup\.automation_backend !== "none" && !detected\[setup\.automation_backend\]\?\.installed\) \{\s*'
        r'if \(desktopManaged && \(setup\.automation_backend === "claude_code" \|\| setup\.automation_backend === "codex"\)\) \{\s*'
        r'const label = setup\.automation_backend === "claude_code" \? "Claude Code" : "Codex";\s*'
        r'log\(`\$\{label\} will be provisioned by Desktop after the core runtime is ready\.`\);\s*'
        r'return \{ setup, detected \};',
        re.S,
    )
    assert pattern.search(TEXT)


def test_desktop_managed_defers_local_model_warmup():
    assert "function runDesktopAwareModelWarmup(" in TEXT
    assert 'Desktop-managed runtime detected — local model warmup deferred during ${reason}.' in TEXT


def test_desktop_managed_detection_uses_only_managed_client_binaries():
    assert 'detectedBy: managedClaudeBin ? "managed_binary" : "missing"' in TEXT
    assert 'detectedBy: managedCodexReady ? "managed_binary" : "missing"' in TEXT
    desktop_branch = TEXT[TEXT.index("function detectInstalledClients()"):TEXT.index("const persistedClaudeBin = readPersistedClaudeCliPath();")]
    assert 'run("which claude"' not in desktop_branch
    assert 'run("which codex"' not in desktop_branch
    assert "readPersistedClaudeCliPath" not in desktop_branch
    assert "codexVendorPresent(managedClaudePrefix())" in desktop_branch


def test_desktop_managed_installers_never_call_host_npm_after_managed_failure():
    assert not re.search(r'if \(desktopManaged\) \{\s*spawnSync\(\s*"npm"', TEXT)
    assert "if (desktopManaged && !npmViaDesktop)" in TEXT
    assert 'if (desktopManaged) return { installed: false, path: "" };' in TEXT


def test_npm_package_keeps_public_codex_bundle_publishable():
    files = set(PACKAGE.get("files") or [])
    assert "codex/openai-codex-0.133.0.tgz" in files
    assert "codex/" not in files
    assert not any(re.search(r"codex/.*-(darwin|linux|win32)-", entry) for entry in files)
