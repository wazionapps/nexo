/**
 * Type declarations for openclaw/plugin-sdk
 *
 * These are minimal type stubs. The actual types come from the openclaw peer dependency.
 */

declare module "openclaw/plugin-sdk/plugin-entry" {
  interface PluginEntryOptions {
    id: string;
    name?: string;
    description?: string;
    kind: "memory" | "tool" | "channel";
    configSchema?: unknown;
    register(api: import("openclaw/plugin-sdk/memory-lancedb").OpenClawPluginApi): void;
  }

  export function definePluginEntry(options: PluginEntryOptions): PluginEntryOptions;
}

declare module "openclaw/plugin-sdk/memory-lancedb" {
  import type { TObject } from "@sinclair/typebox";

  interface ToolDefinition {
    name: string;
    label: string;
    description: string;
    parameters: TObject;
    execute(
      toolCallId: string,
      params: Record<string, unknown>
    ): Promise<{
      content: Array<{ type: "text"; text: string }>;
      details: Record<string, unknown>;
    }>;
  }

  interface ToolRegistrationOptions {
    name?: string;
    names?: string[];
    optional?: boolean;
  }

  type MemoryPromptSectionBuilder = (params: {
    availableTools: Set<string>;
    citationsMode?: "on" | "off";
  }) => string[];

  interface Logger {
    info(message: string): void;
    warn(message: string): void;
    error(message: string): void;
    debug?(message: string): void;
  }

  interface CliRegistration {
    (ctx: { program: import("commander").Command }): void;
  }

  interface ServiceDefinition {
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
    registerCli(fn: CliRegistration, opts?: { commands: string[] }): void;
    registerService(service: ServiceDefinition): void;

    on(
      event: "before_agent_start",
      handler: (event: unknown) => Promise<{ prependContext?: string } | void>
    ): void;
    on(
      event: "agent_end",
      handler: (event: unknown) => Promise<void>
    ): void;

    runtime: {
      tools: Record<string, unknown>;
    };
  }
}
