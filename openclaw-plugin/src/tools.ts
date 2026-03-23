/**
 * Tool definitions for the NEXO Brain OpenClaw memory plugin.
 *
 * Exposes cognitive memory tools as native OpenClaw tools via TypeBox schemas.
 */

import { Type, type TObject } from "@sinclair/typebox";

interface ToolDef {
  name: string;
  nexoName: string;
  label: string;
  description: string;
  parameters: TObject;
}

export const COGNITIVE_TOOLS: ToolDef[] = [
  {
    name: "memory_recall",
    nexoName: "nexo_cognitive_retrieve",
    label: "Recall Memory",
    description:
      "Semantic search across all memories (STM + LTM). Finds memories by meaning, not keywords. Use this to check context before acting.",
    parameters: Type.Object({
      query: Type.String({ description: "What to search for (semantic)" }),
      top_k: Type.Optional(
        Type.Integer({ default: 10, description: "Max results" })
      ),
      min_score: Type.Optional(
        Type.Number({ default: 0.5, description: "Minimum relevance (0-1)" })
      ),
      domain: Type.Optional(
        Type.String({ description: "Filter by domain (e.g. project name)" })
      ),
    }),
  },
  {
    name: "memory_store",
    nexoName: "nexo_learning_add",
    label: "Store Learning",
    description:
      "Store an error pattern or lesson learned. Prevents the same mistake from recurring. The guard system checks these before every code change.",
    parameters: Type.Object({
      category: Type.String({
        description: "Area (e.g. wazion, infrastructure, shopify)",
      }),
      error: Type.String({ description: "What went wrong" }),
      solution: Type.String({ description: "How it was fixed" }),
      reasoning: Type.Optional(
        Type.String({ description: "Why this solution works" })
      ),
    }),
  },
  {
    name: "memory_guard",
    nexoName: "nexo_guard_check",
    label: "Guard Check",
    description:
      "Check past errors and learnings before editing code. Returns relevant warnings, known issues, and DB schemas for the files/area you're about to modify.",
    parameters: Type.Object({
      files: Type.Optional(
        Type.String({ description: "Comma-separated file paths to check" })
      ),
      area: Type.Optional(
        Type.String({ description: "System area (e.g. wazion, shopify)" })
      ),
    }),
  },
  {
    name: "memory_trust",
    nexoName: "nexo_cognitive_trust",
    label: "Update Trust Score",
    description:
      "Adjust alignment score based on user feedback. Higher trust = fewer redundant checks. Events: explicit_thanks (+3), delegation (+2), correction (-3), repeated_error (-7).",
    parameters: Type.Object({
      event: Type.String({
        description:
          "Event type: explicit_thanks, delegation, correction, repeated_error, sibling_detected, proactive_action, override, forgot_followup",
      }),
      context: Type.Optional(
        Type.String({ description: "Brief context for the event" })
      ),
    }),
  },
  {
    name: "memory_dissonance",
    nexoName: "nexo_cognitive_dissonance",
    label: "Check Cognitive Dissonance",
    description:
      "Detect conflicts between a new instruction and established memories. Use when the user gives an instruction that seems to contradict past behavior.",
    parameters: Type.Object({
      instruction: Type.String({
        description: "The new instruction to check against existing memories",
      }),
    }),
  },
  {
    name: "memory_sentiment",
    nexoName: "nexo_cognitive_sentiment",
    label: "Analyze Sentiment",
    description:
      "Analyze the user's current tone and emotional state from recent messages. Adapts agent behavior: frustrated → ultra-concise, flow → suggest backlog items.",
    parameters: Type.Object({
      text: Type.String({ description: "Recent user messages to analyze" }),
    }),
  },
  {
    name: "memory_diary_write",
    nexoName: "nexo_session_diary_write",
    label: "Write Session Diary",
    description:
      "Write a session summary for continuity. The next session reads this to resume context. Include: what was done, decisions made, pending items, mental state.",
    parameters: Type.Object({
      summary: Type.String({ description: "What happened this session" }),
      decisions: Type.Optional(
        Type.String({ description: "Key decisions and reasoning" })
      ),
      pending: Type.Optional(
        Type.String({ description: "What's left to do" })
      ),
      mental_state: Type.Optional(
        Type.String({
          description:
            "Internal state in first person — thread of thought, observations, momentum",
        })
      ),
      domain: Type.Optional(
        Type.String({ description: "Project/domain for multi-session context" })
      ),
    }),
  },
  {
    name: "memory_diary_read",
    nexoName: "nexo_session_diary_read",
    label: "Read Session Diary",
    description:
      "Read recent session diaries for context continuity. Use at session start to resume where the last session left off.",
    parameters: Type.Object({
      last_n: Type.Optional(
        Type.Integer({ default: 3, description: "Number of diaries to read" })
      ),
      last_day: Type.Optional(
        Type.Boolean({
          default: false,
          description: "Read all diaries from last active day",
        })
      ),
      domain: Type.Optional(
        Type.String({ description: "Filter by domain" })
      ),
    }),
  },
  {
    name: "memory_startup",
    nexoName: "nexo_startup",
    label: "Session Startup",
    description:
      "Register a new session. Call once at the start of every conversation. Returns session ID and active sessions.",
    parameters: Type.Object({
      task: Type.Optional(
        Type.String({ default: "Startup", description: "Initial task" })
      ),
    }),
  },
  {
    name: "memory_heartbeat",
    nexoName: "nexo_heartbeat",
    label: "Session Heartbeat",
    description:
      "Update session task and check for messages from other sessions. Call at the start of every user interaction.",
    parameters: Type.Object({
      sid: Type.String({ description: "Session ID from startup" }),
      task: Type.String({ description: "Current task (5-10 words)" }),
    }),
  },
];

export const ALL_TOOL_NAMES = COGNITIVE_TOOLS.map((t) => t.name);
