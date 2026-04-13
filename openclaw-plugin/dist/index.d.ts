/**
 * NEXO Brain — Native OpenClaw Memory Plugin
 *
 * Replaces OpenClaw's default memory system with NEXO's full cognitive architecture:
 * Atkinson-Shiffrin memory model, semantic RAG, trust scoring, guard system,
 * cognitive dissonance detection, and session continuity.
 *
 * Architecture: TypeScript adapter -> MCP Bridge (stdio) -> Python NEXO server
 *
 * Follows the exact plugin patterns from openclaw/extensions/memory-core and
 * openclaw/extensions/memory-lancedb.
 */
declare const _default: import("./api.js").PluginEntryOptions;
export default _default;
