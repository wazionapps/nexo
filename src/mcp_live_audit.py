"""Reconcile live MCP tools with plugin registry and catalog inventories.

This module is intentionally side-effect free: callers pass the live
``tools/list`` result, plugin registry rows and catalog names explicitly.  The
audit never loads plugins, mutates the registry or assumes that "registered"
means "callable" in the current MCP process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable, Mapping, Sequence


LIVE = "live"
DEFERRED = "deferred"
INACTIVE = "inactive"
CATALOG_ONLY = "catalog_only"
MISSING = "missing"


@dataclass(frozen=True)
class PluginRecord:
    filename: str
    tool_names: tuple[str, ...] = ()
    tools_count: int = 0
    source: str = ""
    loaded_at: float | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class ClientProbe:
    client: str = "unknown"
    profile: str = ""
    plugin_mode: str = ""
    tool_names: tuple[str, ...] = ()
    deferred_tools: tuple[str, ...] = ()
    reported_tool_count: int | None = None
    ok: bool = True
    mcp_ready: bool = True


@dataclass(frozen=True)
class ToolStatus:
    name: str
    status: str
    live: bool
    sources: tuple[str, ...]
    reason: str
    plugins: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClientAudit:
    client: str
    profile: str
    plugin_mode: str
    ok: bool
    mcp_ready: bool
    counts: dict[str, int]
    required_missing: tuple[ToolStatus, ...] = ()
    sample_registered_not_live: tuple[str, ...] = ()
    sample_catalog_not_live: tuple[str, ...] = ()
    sample_live_not_cataloged: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def audit_mcp_live(
    probes: Mapping[str, Any] | ClientProbe | Iterable[Mapping[str, Any] | ClientProbe],
    *,
    plugin_records: Iterable[Mapping[str, Any] | PluginRecord] = (),
    catalog_tools: Iterable[Any] = (),
    required_tools: Iterable[Any] = (),
    sample_limit: int = 12,
) -> dict[str, Any]:
    """Build a multi-client MCP availability report.

    Args:
        probes: One probe or an iterable of probes. Each probe should include
            actual live tool names from MCP ``tools/list``. A reported count is
            preserved when names are not available, but name-level diffs need
            names.
        plugin_records: Registry rows such as ``plugin_loader.list_plugins()``
            returns. ``tool_names`` may be comma-separated or iterable.
        catalog_tools: Names or catalog entries with a ``name`` field.
        required_tools: Bootstrap tools that must be live.
        sample_limit: Maximum names included in each sample list.
    """

    records = tuple(coerce_plugin_record(record) for record in plugin_records)
    catalog_names = _normalize_tool_names(catalog_tools)
    required_names = _normalize_tool_names(required_tools)
    client_audits = [
        audit_client_probe(
            coerce_client_probe(probe),
            plugin_records=records,
            catalog_tools=catalog_names,
            required_tools=required_names,
            sample_limit=sample_limit,
        )
        for probe in _coerce_probe_iter(probes)
    ]

    return {
        "ok": all(item.ok and not item.required_missing for item in client_audits),
        "summary": {
            "clients": len(client_audits),
            "clients_with_missing_required": sum(1 for item in client_audits if item.required_missing),
            "plugin_rows": len(records),
            "registered_plugin_tools_raw": sum(record.tools_count for record in records),
            "registered_plugin_tools_unique": len(_plugin_tool_index(records)),
            "catalog_tools": len(catalog_names),
        },
        "clients": [_client_audit_to_dict(item) for item in client_audits],
    }


def audit_client_probe(
    probe: ClientProbe,
    *,
    plugin_records: Iterable[PluginRecord | Mapping[str, Any]] = (),
    catalog_tools: Iterable[Any] = (),
    required_tools: Iterable[Any] = (),
    sample_limit: int = 12,
) -> ClientAudit:
    records = tuple(coerce_plugin_record(record) for record in plugin_records)
    plugin_index = _plugin_tool_index(records)
    plugin_names = set(plugin_index)
    live_names = set(_normalize_tool_names(probe.tool_names))
    deferred_names = set(_normalize_tool_names(probe.deferred_tools))
    catalog_names = set(_normalize_tool_names(catalog_tools))
    required_names = set(_normalize_tool_names(required_tools))

    registered_not_live = plugin_names - live_names
    catalog_not_live = catalog_names - live_names
    live_not_cataloged = live_names - catalog_names if catalog_names else set()
    required_missing = tuple(
        explain_tool_status(
            name,
            live_tools=live_names,
            deferred_tools=deferred_names,
            plugin_records=records,
            catalog_tools=catalog_names,
            plugin_mode=probe.plugin_mode,
        )
        for name in sorted(required_names - live_names)
    )

    live_count = len(live_names)
    if not live_count and probe.reported_tool_count is not None:
        live_count = int(probe.reported_tool_count)

    counts = {
        "live_tools": live_count,
        "live_tool_names_known": len(live_names),
        "deferred_tools": len(deferred_names - live_names),
        "catalog_tools": len(catalog_names),
        "plugin_rows": len(records),
        "registered_plugin_tools_raw": sum(record.tools_count for record in records),
        "registered_plugin_tools_unique": len(plugin_names),
        "registered_plugin_tools_live": len(plugin_names & live_names),
        "registered_not_live": len(registered_not_live),
        "catalog_not_live": len(catalog_not_live),
        "live_not_cataloged": len(live_not_cataloged),
        "required_tools": len(required_names),
        "required_missing": len(required_missing),
    }

    notes = _client_notes(probe, live_names=live_names, plugin_names=plugin_names, catalog_names=catalog_names)
    return ClientAudit(
        client=probe.client,
        profile=probe.profile,
        plugin_mode=probe.plugin_mode,
        ok=bool(probe.ok),
        mcp_ready=bool(probe.mcp_ready),
        counts=counts,
        required_missing=required_missing,
        sample_registered_not_live=_sample(registered_not_live, sample_limit),
        sample_catalog_not_live=_sample(catalog_not_live, sample_limit),
        sample_live_not_cataloged=_sample(live_not_cataloged, sample_limit),
        notes=notes,
    )


def explain_tool_status(
    name: str,
    *,
    live_tools: Iterable[Any] = (),
    deferred_tools: Iterable[Any] = (),
    plugin_records: Iterable[Mapping[str, Any] | PluginRecord] = (),
    catalog_tools: Iterable[Any] = (),
    plugin_mode: str = "",
) -> ToolStatus:
    """Classify one tool for the current MCP process."""

    clean_name = str(name or "").strip()
    live_names = set(_normalize_tool_names(live_tools))
    deferred_names = set(_normalize_tool_names(deferred_tools))
    records = tuple(coerce_plugin_record(record) for record in plugin_records)
    plugin_index = _plugin_tool_index(records)
    catalog_names = set(_normalize_tool_names(catalog_tools))

    sources: list[str] = []
    if clean_name in live_names:
        sources.append("live")
    if clean_name in deferred_names:
        sources.append("deferred")
    if clean_name in plugin_index:
        sources.append("plugin_registry")
    if clean_name in catalog_names:
        sources.append("catalog")

    if clean_name in live_names:
        return ToolStatus(
            name=clean_name,
            status=LIVE,
            live=True,
            sources=tuple(sources),
            reason="Callable in the current MCP tools/list result.",
            plugins=tuple(sorted(plugin_index.get(clean_name, ()))),
        )
    if clean_name in deferred_names:
        return ToolStatus(
            name=clean_name,
            status=DEFERRED,
            live=False,
            sources=tuple(sources),
            reason="The client surfaced the tool as deferred; resolve its schema before treating it as absent.",
            plugins=tuple(sorted(plugin_index.get(clean_name, ()))),
        )
    if clean_name in plugin_index:
        mode = str(plugin_mode or "unknown")
        return ToolStatus(
            name=clean_name,
            status=INACTIVE,
            live=False,
            sources=tuple(sources),
            reason=f"Registered plugin tool, but not callable in this live process (plugin_mode={mode}).",
            plugins=tuple(sorted(plugin_index.get(clean_name, ()))),
        )
    if clean_name in catalog_names:
        return ToolStatus(
            name=clean_name,
            status=CATALOG_ONLY,
            live=False,
            sources=tuple(sources),
            reason="Cataloged/documented, but absent from live tools/list and plugin registry.",
        )
    return ToolStatus(
        name=clean_name,
        status=MISSING,
        live=False,
        sources=(),
        reason="Absent from live tools/list, deferred tools, plugin registry and catalog.",
    )


def coerce_plugin_record(record: Mapping[str, Any] | PluginRecord) -> PluginRecord:
    if isinstance(record, PluginRecord):
        return record
    filename = str(record.get("filename") or record.get("plugin") or "").strip()
    tool_names = _normalize_tool_names(record.get("tool_names") or record.get("tools") or record.get("names") or ())
    raw_count = record.get("tools_count", record.get("tool_count", None))
    try:
        tools_count = int(raw_count) if raw_count is not None else len(tool_names)
    except (TypeError, ValueError):
        tools_count = len(tool_names)
    return PluginRecord(
        filename=filename,
        tool_names=tool_names,
        tools_count=tools_count,
        source=str(record.get("source") or record.get("created_by") or "").strip(),
        loaded_at=_float_or_none(record.get("loaded_at")),
        enabled=_bool_or_none(record.get("enabled")),
    )


def coerce_client_probe(probe: Mapping[str, Any] | ClientProbe) -> ClientProbe:
    if isinstance(probe, ClientProbe):
        return probe
    tool_names = _normalize_tool_names(probe.get("tool_names") or probe.get("tools") or ())
    reported_count = probe.get("tool_count", probe.get("reported_tool_count", None))
    try:
        reported_tool_count = int(reported_count) if reported_count is not None else None
    except (TypeError, ValueError):
        reported_tool_count = None
    return ClientProbe(
        client=str(probe.get("client") or "unknown"),
        profile=str(probe.get("profile") or probe.get("name") or ""),
        plugin_mode=str(probe.get("plugin_mode") or ""),
        tool_names=tool_names,
        deferred_tools=_normalize_tool_names(probe.get("deferred_tools") or ()),
        reported_tool_count=reported_tool_count,
        ok=bool(probe.get("ok", True)),
        mcp_ready=bool(probe.get("mcp_ready", True)),
    )


def parse_probe_json(payload: str | Mapping[str, Any]) -> ClientProbe:
    """Parse a saved MCP probe JSON payload into a ``ClientProbe``."""

    if isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = dict(payload)
    return coerce_client_probe(data)


def format_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact markdown report for specs or handoff notes."""

    clients = list(report.get("clients") or [])
    lines = [
        "## MCP live/catalog audit",
        "",
        "| Client | Profile | Plugin mode | Live | Catalog | Plugin rows | Registered tools | Registered not live | Required missing |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for client in clients:
        counts = client.get("counts") or {}
        lines.append(
            "| {client} | {profile} | {plugin_mode} | {live} | {catalog} | {plugin_rows} | {registered_raw} | {registered_not_live} | {required_missing} |".format(
                client=_md(client.get("client") or "unknown"),
                profile=_md(client.get("profile") or "-"),
                plugin_mode=_md(client.get("plugin_mode") or "-"),
                live=counts.get("live_tools", 0),
                catalog=counts.get("catalog_tools", 0),
                plugin_rows=counts.get("plugin_rows", 0),
                registered_raw=counts.get("registered_plugin_tools_raw", 0),
                registered_not_live=counts.get("registered_not_live", 0),
                required_missing=counts.get("required_missing", 0),
            )
        )

    lines.extend(["", "### Notes", ""])
    any_notes = False
    for client in clients:
        label = client.get("client") or "unknown"
        for note in client.get("notes") or ():
            any_notes = True
            lines.append(f"- `{_md(label)}`: {_md(note)}")
    if not any_notes:
        lines.append("- No reconciliation notes.")

    lines.extend(["", "### Required missing", ""])
    missing_rows = [
        (client.get("client") or "unknown", item)
        for client in clients
        for item in client.get("required_missing") or ()
    ]
    if not missing_rows:
        lines.append("- No required tools missing from live MCP.")
    for client, item in missing_rows:
        lines.append(
            "- `{client}` `{name}`: {status} - {reason}".format(
                client=_md(client),
                name=_md(item.get("name")),
                status=_md(item.get("status")),
                reason=_md(item.get("reason")),
            )
        )
    return "\n".join(lines)


def _client_audit_to_dict(audit: ClientAudit) -> dict[str, Any]:
    data = asdict(audit)
    data["required_missing"] = [asdict(item) for item in audit.required_missing]
    return data


def _coerce_probe_iter(
    probes: Mapping[str, Any] | ClientProbe | Iterable[Mapping[str, Any] | ClientProbe],
) -> tuple[Mapping[str, Any] | ClientProbe, ...]:
    if isinstance(probes, ClientProbe):
        return (probes,)
    if isinstance(probes, Mapping):
        if "clients" in probes and not ("tool_names" in probes or "tools" in probes):
            return tuple(probes.get("clients") or ())
        return (probes,)
    return tuple(probes)


def _normalize_tool_names(value: Any) -> tuple[str, ...]:
    names = _flatten_tool_names(value)
    return tuple(sorted({name for name in names if name}))


def _flatten_tool_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return _flatten_tool_names(json.loads(text))
            except Exception:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, Mapping):
        if "name" in value:
            return [str(value.get("name") or "").strip()]
        if "tool_names" in value:
            return _flatten_tool_names(value.get("tool_names"))
        if "tools" in value:
            return _flatten_tool_names(value.get("tools"))
        return []
    if isinstance(value, Iterable):
        names: list[str] = []
        for item in value:
            names.extend(_flatten_tool_names(item))
        return names
    return [str(value).strip()]


def _plugin_tool_index(records: Iterable[PluginRecord]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for record in records:
        plugin = record.filename or "<unknown-plugin>"
        for name in record.tool_names:
            index.setdefault(name, set()).add(plugin)
    return index


def _client_notes(
    probe: ClientProbe,
    *,
    live_names: set[str],
    plugin_names: set[str],
    catalog_names: set[str],
) -> tuple[str, ...]:
    notes: list[str] = []
    if plugin_names and plugin_names - live_names:
        notes.append(
            "registered plugin tools are not automatically callable; current live MCP is missing "
            f"{len(plugin_names - live_names)} registered plugin tool(s)"
        )
    if catalog_names and catalog_names - live_names:
        notes.append(
            "catalog entries are inventory, not live availability; current live MCP is missing "
            f"{len(catalog_names - live_names)} cataloged tool(s)"
        )
    if probe.reported_tool_count is not None and not probe.tool_names:
        notes.append("probe reported a tool count without names, so name-level diffs are incomplete")
    if probe.plugin_mode:
        notes.append(f"plugin_mode={probe.plugin_mode}")
    return tuple(notes)


def _sample(names: Iterable[str], limit: int) -> tuple[str, ...]:
    return tuple(sorted(names)[: max(0, int(limit))])


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "si"}


def _md(value: Any) -> str:
    text = str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
    return text.strip()
