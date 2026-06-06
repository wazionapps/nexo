from pathlib import Path


def test_postinstall_uses_execfilesync_with_execpath():
    text = Path("bin/postinstall.js").read_text()

    assert "execFileSync" in text
    assert "process.execPath" in text
    assert '[INSTALLER, "--yes"]' in text
    assert "execSync(`node " not in text


def test_installer_copies_product_knowledge_package():
    text = Path("bin/nexo-brain.js").read_text()

    assert "function getCoreRuntimePackages()" in text
    assert '"product_knowledge"' in text
