/**
 * Re-export OpenClaw plugin SDK types.
 *
 * In the monorepo, this would be:
 *   export * from "openclaw/plugin-sdk/memory-lancedb";
 *
 * Since this plugin is standalone (not in the openclaw monorepo),
 * we provide compatible type definitions inline.
 */
/**
 * Defines a plugin entry point.
 * In the openclaw monorepo this is the real SDK function.
 * Standalone plugins export this as `export default definePluginEntry({...})`.
 */
export function definePluginEntry(options) {
    return options;
}
