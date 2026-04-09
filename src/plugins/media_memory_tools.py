"""Multimodal memory reference tools."""

import media_memory


def handle_media_memory_add(
    file_path: str = "",
    url: str = "",
    title: str = "",
    description: str = "",
    tags: str = "",
    domain: str = "",
    source_type: str = "",
    source_id: str = "",
    metadata: str = "",
) -> str:
    result = media_memory.add_media_memory(
        file_path=file_path,
        url=url,
        title=title,
        description=description,
        tags=tags,
        domain=domain,
        source_type=source_type,
        source_id=source_id,
        metadata=metadata,
    )
    if result.get("error"):
        return f"ERROR: {result['error']}"
    location = result.get("file_path") or result.get("url") or "n/a"
    return f"Media memory #{result['id']} [{result['media_type']}] stored: {location}"


def handle_media_memory_search(
    query: str = "",
    media_type: str = "",
    domain: str = "",
    tag: str = "",
    limit: int = 20,
) -> str:
    items = media_memory.search_media_memories(
        query=query,
        media_type=media_type,
        domain=domain,
        tag=tag,
        limit=limit,
    )
    if not items:
        return "No media memories found."
    lines = [f"MEDIA MEMORIES — {len(items)} result(s):", ""]
    for item in items:
        lines.append(f"  #{item['id']} [{item['media_type']}] {item['title'][:120]}")
        lines.append(f"    {item.get('file_path') or item.get('url') or 'n/a'}")
        if item.get("description"):
            lines.append(f"    {item['description'][:180]}")
        if item.get("tags"):
            lines.append(f"    tags: {item['tags']}")
    return "\n".join(lines)


def handle_media_memory_get(media_id: int) -> str:
    item = media_memory.get_media_memory(media_id)
    if not item:
        return f"Media memory #{media_id} not found."
    lines = [
        f"MEDIA MEMORY #{item['id']}",
        f"  type: {item['media_type']}",
        f"  title: {item['title']}",
        f"  location: {item.get('file_path') or item.get('url') or 'n/a'}",
        f"  domain: {item.get('domain') or 'n/a'}",
        f"  source: {item.get('source_type') or 'n/a'}:{item.get('source_id') or ''}",
    ]
    if item.get("description"):
        lines.append(f"  description: {item['description']}")
    if item.get("tags"):
        lines.append(f"  tags: {item['tags']}")
    if item.get("metadata"):
        lines.append(f"  metadata: {item['metadata']}")
    return "\n".join(lines)


def handle_media_memory_stats() -> str:
    stats = media_memory.media_memory_stats()
    return (
        "MEDIA MEMORY STATS\n"
        f"  total: {stats['total']}\n"
        f"  backend: {stats['backend']}\n"
        f"  by_type: {stats['by_type']}\n"
        f"  by_domain: {stats['by_domain']}"
    )


TOOLS = [
    (handle_media_memory_add, "nexo_media_memory_add", "Store a non-text artifact as first-class media memory metadata."),
    (handle_media_memory_search, "nexo_media_memory_search", "Search media memories by text, type, tag, or domain."),
    (handle_media_memory_get, "nexo_media_memory_get", "Inspect one stored media memory."),
    (handle_media_memory_stats, "nexo_media_memory_stats", "Stats for the multimodal/media memory layer."),
]
