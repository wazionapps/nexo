from pathlib import Path
import re


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "nexo-brain.js"
TEXT = SOURCE.read_text(encoding="utf-8")


def test_desktop_managed_defers_claude_install_until_final_sync():
    assert 'if (desktopManaged && client === "claude_code") {' in TEXT
    assert 'Claude Code install deferred to Desktop final sync.' in TEXT


def test_desktop_managed_keeps_claude_automation_enabled_for_final_sync():
    pattern = re.compile(
        r'if \(setup\.automation_enabled && setup\.automation_backend !== "none" && !detected\[setup\.automation_backend\]\?\.installed\) \{\s*'
        r'if \(desktopManaged && setup\.automation_backend === "claude_code"\) \{\s*'
        r'log\("Claude Code will be provisioned by Desktop after the core runtime is ready\."\);\s*'
        r'return \{ setup, detected \};',
        re.S,
    )
    assert pattern.search(TEXT)


def test_desktop_managed_defers_local_model_warmup():
    assert "function runDesktopAwareModelWarmup(" in TEXT
    assert 'Desktop-managed runtime detected — local model warmup deferred during ${reason}.' in TEXT
