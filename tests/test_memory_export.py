"""Tests for markdown memory export."""


def test_memory_export_writes_markdown_bundle(tmp_path):
    import claim_graph
    import media_memory
    from plugins.memory_export import handle_memory_export

    asset = tmp_path / "diagram.png"
    asset.write_bytes(b"png")

    claim_graph.add_claim(text="Exported claims should be readable.", domain="nexo", evidence="spec")
    media_memory.add_media_memory(file_path=str(asset), title="Diagram", description="Export test asset", tags="image")

    target = tmp_path / "export"
    result = handle_memory_export(output_dir=str(target))
    assert "Memory export written" in result
    assert (target / "README.md").is_file()
    assert (target / "claims.md").is_file()
    assert (target / "media.md").is_file()


def test_memory_export_redacts_sensitive_claims_and_media_paths(tmp_path):
    import claim_graph
    import media_memory
    from plugins.memory_export import handle_memory_export

    claim_graph.add_claim(
        text="Claim leaked /Users/franciscoc/private token=raw-secret-value from 192.168.1.7",
        domain="nexo",
        evidence="spec",
    )
    media_memory.add_media_memory(
        file_path="/Users/franciscoc/private/diagram.png",
        title="Diagram token=raw-secret-value",
        description="Export test asset",
        tags="image",
    )

    target = tmp_path / "export"
    handle_memory_export(output_dir=str(target))

    combined = "\n".join(path.read_text() for path in (target / "claims.md", target / "media.md"))
    assert "/Users/franciscoc" not in combined
    assert "192.168.1.7" not in combined
    assert "raw-secret-value" not in combined
    assert "[redacted_path]" in combined


def test_memory_export_defaults_to_runtime_exports_dir(tmp_path, monkeypatch):
    import os
    from plugins.memory_export import handle_memory_export

    monkeypatch.setenv("NEXO_HOME", str(tmp_path / ".nexo"))
    result = handle_memory_export()

    assert "Memory export written to" in result
    export_root = tmp_path / ".nexo" / "runtime" / "exports" / "memory"
    assert export_root.is_dir()
    generated = sorted(export_root.iterdir())
    assert generated, "expected a timestamped export directory under runtime/exports/memory"
    assert (generated[-1] / "README.md").is_file()
