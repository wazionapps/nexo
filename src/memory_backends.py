from __future__ import annotations

"""Explicit backend registry for memory expansion layers.

NEXO's historical memory system is still heavily SQLite-shaped, but newer layers
should not keep backend assumptions implicit forever. This module introduces a
small registry/contract that expansion surfaces can use today while SQLite
remains the default backend.
"""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class MemoryBackendInfo:
    key: str
    label: str
    description: str
    supports: tuple[str, ...]
    maturity: str = "stable"


_REGISTRY: dict[str, MemoryBackendInfo] = {}


def register_backend(info: MemoryBackendInfo) -> None:
    _REGISTRY[info.key] = info


def active_backend_key() -> str:
    return (os.environ.get("NEXO_MEMORY_BACKEND", "sqlite") or "sqlite").strip().lower()


def get_backend(key: str = "") -> MemoryBackendInfo:
    selected = (key or active_backend_key()).strip().lower()
    return _REGISTRY.get(selected, _REGISTRY["sqlite"])


def list_backends() -> list[dict]:
    active = active_backend_key()
    results = []
    for key in sorted(_REGISTRY):
        info = _REGISTRY[key]
        item = {
            "key": info.key,
            "label": info.label,
            "description": info.description,
            "supports": list(info.supports),
            "maturity": info.maturity,
            "active": info.key == active,
        }
        results.append(item)
    return results


register_backend(
    MemoryBackendInfo(
        key="sqlite",
        label="SQLite + FTS5",
        description="Local-first default backend used by NEXO runtime surfaces.",
        supports=(
            "cognitive_core",
            "claims",
            "media_memory",
            "user_state",
            "memory_export",
            "auto_flush",
        ),
    )
)
