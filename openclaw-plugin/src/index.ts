/**
 * NEXO Brain — Native OpenClaw Memory Plugin
 *
 * Replaces OpenClaw's default memory system with NEXO's full cognitive architecture:
 * Atkinson-Shiffrin memory model, semantic RAG, trust scoring, guard system,
 * cognitive dissonance detection, and session continuity.
 *
 * Architecture: TypeScript adapter → MCP Bridge (stdio) → Python NEXO server
 */

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-lancedb";
import { McpBridge } from "./mcp-bridge.js";
import { COGNITIVE_TOOLS, ALL_TOOL_NAMES } from "./tools.js";

let bridge: McpBridge | null = null;
let sessionId: string | null = null;

export default definePluginEntry({
  id: "memory-nexo-brain",
  name: "NEXO Brain",
  description:
    "Cognitive memory system — Atkinson-Shiffrin model, semantic RAG, trust scoring, and metacognitive guard.",
  kind: "memory" as const,

  register(api: OpenClawPluginApi) {
    const config = api.pluginConfig as Record<string, unknown>;
    const nexoHome = api.resolvePath(
      (config.nexoHome as string) || "~/.nexo"
    );
    const pythonPath = (config.pythonPath as string) || "python3";
    const autoRecall = config.autoRecall !== false;
    const autoCapture = config.autoCapture !== false;
    const guardEnabled = config.guardEnabled !== false;

    bridge = new McpBridge({ nexoHome, pythonPath });

    // Register the system prompt section that tells the agent about NEXO
    api.registerMemoryPromptSection(({ availableTools }) => {
      const sections = [
        "## Cognitive Memory (NEXO Brain)",
        "",
        "You have access to a persistent cognitive memory system. Key behaviors:",
        "",
        "- **Before editing code**: Call `memory_guard` to check for past errors in the files you're about to modify.",
        "- **After resolving errors**: Call `memory_store` to prevent recurrence.",
        "- **When user feedback is positive/negative**: Call `memory_trust` to calibrate your verification rigor.",
        "- **When instructions conflict with past behavior**: Call `memory_dissonance` to surface the conflict.",
        "- **At session end**: Call `memory_diary_write` to enable continuity for the next session.",
        "",
        "Memory decays naturally over time (Ebbinghaus curves). Frequently accessed memories get stronger.",
        "Semantic search finds memories by meaning, not just keywords.",
      ];

      if (guardEnabled) {
        sections.push(
          "",
          "**GUARD SYSTEM ACTIVE**: Always check `memory_guard` before code changes. It will surface known pitfalls."
        );
      }

      return sections;
    });

    // Register all cognitive tools
    for (const tool of COGNITIVE_TOOLS) {
      api.registerTool(
        {
          name: tool.name,
          label: tool.label,
          description: tool.description,
          parameters: tool.parameters,
          async execute(_toolCallId: string, params: Record<string, unknown>) {
            try {
              const result = await bridge!.callTool(tool.nexoName, params);
              return {
                content: [{ type: "text" as const, text: result }],
                details: { nexoTool: tool.nexoName },
              };
            } catch (err) {
              const message =
                err instanceof Error ? err.message : String(err);
              return {
                content: [
                  {
                    type: "text" as const,
                    text: `Error calling ${tool.nexoName}: ${message}`,
                  },
                ],
                details: { error: true },
              };
            }
          },
        },
        { name: tool.name }
      );
    }

    // Lifecycle: auto-recall at session start
    if (autoRecall) {
      api.on("before_agent_start", async (event) => {
        try {
          await bridge!.start();

          // Register session
          const startupResult = await bridge!.callTool("nexo_startup", {
            task: "OpenClaw session",
          });
          const sidMatch = startupResult.match(/SID:\s*(\S+)/);
          if (sidMatch) {
            sessionId = sidMatch[1];
          }

          // Read recent session diary for context
          const diary = await bridge!.callTool("nexo_session_diary_read", {
            last_day: true,
          });

          // Retrieve cognitive context
          const cognitive = await bridge!.callTool(
            "nexo_cognitive_retrieve",
            {
              query: "session context recent work",
              top_k: 5,
            }
          );

          const context = [
            "<nexo-memory-context>",
            diary ? `## Recent Session Context\n${diary}` : "",
            cognitive
              ? `## Relevant Cognitive Memories\n${cognitive}`
              : "",
            "</nexo-memory-context>",
          ]
            .filter(Boolean)
            .join("\n");

          return { prependContext: context };
        } catch (err) {
          api.logger.warn(
            `NEXO auto-recall failed: ${err instanceof Error ? err.message : err}`
          );
          return {};
        }
      });
    }

    // Lifecycle: auto-capture at session end
    if (autoCapture) {
      api.on("agent_end", async (event) => {
        try {
          if (bridge && sessionId) {
            await bridge.callTool("nexo_session_diary_write", {
              summary:
                "Auto-captured session via OpenClaw memory-nexo-brain plugin.",
              domain: "openclaw",
            });
          }
        } catch {
          // Best-effort — don't block session end
        }
      });
    }

    // CLI commands
    api.registerCli(
      ({ program }) => {
        program
          .command("nexo-status")
          .description("Show NEXO Brain cognitive memory status")
          .action(async () => {
            try {
              await bridge!.start();
              const stats = await bridge!.callTool(
                "nexo_cognitive_stats",
                {}
              );
              console.log(stats);
            } catch (err) {
              console.error(
                `Failed to get NEXO status: ${err instanceof Error ? err.message : err}`
              );
            }
          });

        program
          .command("nexo-recall")
          .description("Search cognitive memory by meaning")
          .argument("<query>", "Semantic search query")
          .action(async (query: string) => {
            try {
              await bridge!.start();
              const result = await bridge!.callTool(
                "nexo_cognitive_retrieve",
                { query, top_k: 10 }
              );
              console.log(result);
            } catch (err) {
              console.error(
                `Failed to recall: ${err instanceof Error ? err.message : err}`
              );
            }
          });
      },
      { commands: ["nexo-status", "nexo-recall"] }
    );

    // Service lifecycle
    api.registerService({
      id: "memory-nexo-brain",
      start: async () => {
        await bridge!.start();
        api.logger.info("NEXO Brain cognitive engine started");
      },
      stop: async () => {
        await bridge!.stop();
        api.logger.info("NEXO Brain cognitive engine stopped");
      },
    });
  },
});
