from pathlib import Path


def test_postinstall_uses_execfilesync_with_execpath():
    text = Path("bin/postinstall.js").read_text()

    assert "execFileSync" in text
    assert "process.execPath" in text
    assert 'path.join(__dirname, "nexo-brain.js"), "--yes"' in text
    assert "execSync(`node " not in text

