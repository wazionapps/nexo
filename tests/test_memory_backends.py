"""Tests for explicit memory backend registry/status."""

import importlib


def test_memory_backend_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("NEXO_MEMORY_BACKEND", raising=False)
    backends = importlib.import_module("memory_backends")

    active = backends.get_backend()

    assert active.key == "sqlite"
    assert "media_memory" in active.supports
    assert any(item["active"] and item["key"] == "sqlite" for item in backends.list_backends())


def test_memory_backend_unknown_selection_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("NEXO_MEMORY_BACKEND", "future-store")
    backends = importlib.import_module("memory_backends")

    active = backends.get_backend()

    assert active.key == "sqlite"
