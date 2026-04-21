from pathlib import Path


def test_check_context_uses_runtime_state_dir_instead_of_root_state():
    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "scripts"
        / "check-context.py"
    ).read_text(encoding="utf-8")

    assert "paths.runtime_state_dir()" in script
    assert 'NEXO_HOME / "state"' not in script
