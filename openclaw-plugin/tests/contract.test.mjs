import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import pluginEntry from "../dist/index.js";
import { COGNITIVE_TOOLS } from "../dist/tools.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const pluginRoot = path.resolve(__dirname, "..");

function buildFakeApi() {
  const tools = [];
  const hooks = new Map();
  const promptSections = [];

  return {
    tools,
    hooks,
    promptSections,
    api: {
      pluginConfig: {},
      config: {},
      resolvePath(input) {
        return input.replace(/^~/, "/tmp/fake-home");
      },
      logger: {
        info() {},
        warn() {},
        error() {},
      },
      registerMemoryPromptSection(builder) {
        promptSections.push(builder);
      },
      registerTool(definition, opts = {}) {
        tools.push({ definition, opts });
      },
      registerCli() {},
      registerService() {},
      on(event, handler) {
        hooks.set(event, handler);
      },
      runtime: {
        tools: {},
      },
    },
  };
}

test("OpenClaw plugin exports a memory plugin entry", () => {
  assert.equal(pluginEntry.id, "memory-nexo-brain");
  assert.equal(pluginEntry.kind, "memory");
  assert.equal(typeof pluginEntry.register, "function");
});

test("OpenClaw plugin registers the expected tools and lifecycle hooks", () => {
  const ctx = buildFakeApi();
  pluginEntry.register(ctx.api);

  assert.equal(ctx.tools.length, COGNITIVE_TOOLS.length);
  assert.equal(ctx.promptSections.length, 1);
  assert.ok(ctx.hooks.has("before_agent_start"));
  assert.ok(ctx.hooks.has("agent_end"));

  const toolNames = new Set(ctx.tools.map((entry) => entry.definition.name));
  for (const tool of COGNITIVE_TOOLS) {
    assert.ok(toolNames.has(tool.name), `missing tool ${tool.name}`);
  }
});

test("memory prompt section teaches recall and guard usage", () => {
  const ctx = buildFakeApi();
  pluginEntry.register(ctx.api);
  const builder = ctx.promptSections[0];
  const lines = builder({
    availableTools: new Set(["memory_recall", "memory_guard", "memory_store"]),
  });

  const body = lines.join("\n");
  assert.match(body, /memory_recall/);
  assert.match(body, /memory_guard/);
  assert.match(body, /memory_store/);
  assert.match(body, /Atkinson-Shiffrin/i);
});

test("packaged manifest stays aligned with plugin entry", () => {
  const manifestPath = path.join(pluginRoot, "openclaw.plugin.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

  assert.equal(manifest.id, pluginEntry.id);
  assert.equal(manifest.kind, pluginEntry.kind);
  assert.equal(manifest.configSchema.type, "object");
  assert.equal(manifest.configSchema.additionalProperties, false);
  assert.ok(manifest.configSchema.properties.nexoHome);
  assert.ok(manifest.configSchema.properties.pythonPath);
});

test("bridge targets the packaged server path and synced client version", () => {
  const bridgePath = path.join(pluginRoot, "src", "mcp-bridge.ts");
  const packagePath = path.join(pluginRoot, "package.json");
  const bridgeText = fs.readFileSync(bridgePath, "utf8");
  const packageJson = JSON.parse(fs.readFileSync(packagePath, "utf8"));
  const escapedVersion = String(packageJson.version).replaceAll(".", "\\.");

  assert.match(bridgeText, /resolve\(this\.config\.nexoHome,\s*"server\.py"\)/);
  assert.doesNotMatch(bridgeText, /resolve\(this\.config\.nexoHome,\s*"src",\s*"server\.py"\)/);
  assert.match(
    bridgeText,
    new RegExp(`clientInfo:\\s*\\{ name: "openclaw-memory-nexo-brain", version: "${escapedVersion}" \\}`),
  );
});
