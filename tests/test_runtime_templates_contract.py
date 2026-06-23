from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_data_smoke_template_is_indexed_and_requires_channel_coverage():
    template_path = ROOT / "docs" / "templates" / "smoke-test-real-data.md"
    index = (ROOT / "docs" / "runtime-templates.md").read_text(encoding="utf-8")
    text = template_path.read_text(encoding="utf-8")

    assert "docs/templates/smoke-test-real-data.md" in index
    assert "task_type='execute'" in text
    for engine in ("booking", "payment", "voice routing", "availability"):
        assert engine in text
    for channel in ("web", "voice", "whatsapp", "admin"):
        assert channel in text
    for edge in ("straddle", "midnight", "year boundary"):
        assert edge in text
    assert "The smoke is not valid with fabricated records only" in text
