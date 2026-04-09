"""Tests for multimodal/media memory layer."""


def test_media_memory_add_deduplicates_local_path(tmp_path):
    import media_memory

    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"fake-image")

    first = media_memory.add_media_memory(
        file_path=str(screenshot),
        title="Architecture screenshot",
        description="Claims dashboard mockup",
        tags="image,ui",
        domain="nexo",
    )
    second = media_memory.add_media_memory(
        file_path=str(screenshot),
        title="Architecture screenshot v2",
        description="Claims dashboard mockup updated",
        tags="image,ui,dashboard",
        domain="nexo",
    )

    assert first["id"] == second["id"]
    assert second["media_type"] == "image"
    assert second["metadata"]["size_bytes"] == len(b"fake-image")


def test_media_memory_search_filters_by_text_and_type(tmp_path):
    import media_memory

    audio = tmp_path / "briefing.mp3"
    audio.write_bytes(b"fake-audio")
    media_memory.add_media_memory(
        file_path=str(audio),
        title="Morning briefing",
        description="Audio summary of the day",
        tags="audio,briefing",
        domain="ops",
    )

    results = media_memory.search_media_memories(query="briefing", media_type="audio", limit=10)
    assert len(results) == 1
    assert results[0]["title"] == "Morning briefing"
