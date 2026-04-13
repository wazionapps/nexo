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
