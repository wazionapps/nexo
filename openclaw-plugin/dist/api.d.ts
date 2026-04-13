/**
 * Re-export OpenClaw plugin SDK types.
 *
 * In the monorepo, this would be:
 *   export * from "openclaw/plugin-sdk/memory-lancedb";
 *
 * Since this plugin is standalone (not in the openclaw monorepo),
 * we provide compatible type definitions inline.
 */
import type { TObject } from "@sinclair/typebox";
export interface ToolDefinition {
    name: string;
    label: string;
    description: string;
    parameters: TObject;
    execute(toolCallId: string, params: Record<string, unknown>): Promise<{
        content: Array<{
            type: "text";
            text: string;
        }>;
        details: Record<string, unknown>;
    }>;
}
export interface ToolRegistrationOptions {
    name?: string;
    names?: string[];
    optional?: boolean;
}
export type MemoryPromptSectionBuilder = (params: {
    availableTools: Set<string>;
    citationsMode?: "on" | "off";
}) => string[];
export interface Logger {
    info(message: string): void;
    warn(message: string): void;
    error(message: string): void;
    debug?(message: string): void;
}
export interface ServiceDefinition {
    id: string;
    start(): void | Promise<void>;
    stop(): void | Promise<void>;
}
export interface OpenClawPluginApi {
    pluginConfig: Record<string, unknown>;
    config: Record<string, unknown>;
    resolvePath(input: string): string;
    logger: Logger;
    registerMemoryPromptSection(builder: MemoryPromptSectionBuilder): void;
    registerTool(definition: ToolDefinition, opts?: ToolRegistrationOptions): void;
    registerCli(fn: (ctx: {
        program: {
            command(name: string): any;
        };
    }) => void, opts?: {
        commands: string[];
    }): void;
    registerService(service: ServiceDefinition): void;
    on(event: "before_agent_start", handler: (event: unknown) => Promise<{
        prependContext?: string;
    } | void>): void;
    on(event: "agent_end", handler: (event: unknown) => Promise<void>): void;
    runtime: {
        tools: Record<string, unknown>;
    };
}
export interface PluginEntryOptions {
    id: string;
    name?: string;
    description?: string;
    kind: "memory" | "tool" | "channel";
    configSchema?: unknown;
    register(api: OpenClawPluginApi): void;
}
/**
 * Defines a plugin entry point.
 * In the openclaw monorepo this is the real SDK function.
 * Standalone plugins export this as `export default definePluginEntry({...})`.
 */
export declare function definePluginEntry(options: PluginEntryOptions): PluginEntryOptions;
